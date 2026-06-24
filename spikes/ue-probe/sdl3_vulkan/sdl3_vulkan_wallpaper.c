/* sdl3_vulkan_wallpaper.c — ADR-0029 §A PoC-0b.
 *
 * GOAL: prove the EXACT rendering path the real UE patch (rung 3) will use to
 * become the desktop wallpaper:
 *
 *   SDL3 creates a bare wl_surface (NOT an xdg_toplevel) via the property
 *   SDL_PROP_WINDOW_CREATE_WAYLAND_SURFACE_ROLE_CUSTOM_BOOLEAN=true
 *      -> we fetch the raw wl_display + wl_surface back out of the SDL window
 *      -> we assign that wl_surface the zwlr_layer_shell_v1 BACKGROUND role
 *         ourselves (the CONFIRMED-correct wallpaper role on this KWin 6.6.5)
 *      -> we hand the SAME wl_surface to Vulkan (SDL_Vulkan_CreateSurface),
 *         build a swapchain, and render a live clear-color loop.
 *
 * This is the bridge between the (already-passed) gtk4 layer-shell stand-in and
 * the real UE patch: the gtk4 path could NOT do Vulkan-on-a-custom-role surface,
 * and the UE path will. PoC-0b is the smallest thing that does both at once.
 *
 * Rung 1 is ALREADY PASSED: a foreign zwlr_layer_shell_v1 BACKGROUND surface on
 * this KWin lands at stacking index [1] — above only the Plasma desktop
 * containment [0] (covers wallpaper+icons, ACCEPTED), below every app window and
 * the panel. So BACKGROUND is the right role; this PoC re-uses those exact params
 * (4-edge anchor, exclusive_zone=-1, keyboard none) but drives the pixels with
 * Vulkan through an SDL3-minted custom-role surface.
 *
 * GPU footprint: one tiny clear-color swapchain (FIFO, double-buffered) at the
 * output resolution — ~tens of MB. No pipelines, no geometry, no UE.
 *
 * SAFETY: clears to a DISTINCT dark green (#0b3618) every frame so it reads as an
 * obvious test, never as a glitch. SIGINT/SIGTERM tears it down; nothing persists.
 *
 * Build:  ./build.sh        Run (VISUAL — human only):  ./sdl3_vulkan_wallpaper
 * Stop:   Ctrl-C   or   pkill -f '[s]dl3_vulkan_wallpaper'
 */

#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <wayland-client.h>

#include <SDL3/SDL.h>
#include <SDL3/SDL_vulkan.h>

#include <vulkan/vulkan.h>

#include "wlr-layer-shell-unstable-v1-client-protocol.h"

/* ---- distinct test clear color: dark green #0b3618 (linear-ish, opaque) ---- */
#define CLEAR_R (0x0b / 255.0f)
#define CLEAR_G (0x36 / 255.0f)
#define CLEAR_B (0x18 / 255.0f)

static volatile sig_atomic_t g_running = 1;
static void on_sig(int s) { (void)s; g_running = 0; }

/* ----------------------------------------------------------------------------
 * Wayland: registry + layer-shell glue (driving the SDL-minted wl_surface)
 * -------------------------------------------------------------------------- */
static struct zwlr_layer_shell_v1 *g_layer_shell = NULL;

/* configure: ack + adopt the suggested size (anchored 4-edge ⇒ full output) */
static uint32_t g_cfg_w = 0, g_cfg_h = 0;
static bool     g_configured = false;
static bool     g_closed = false;

static void ls_configure(void *data, struct zwlr_layer_surface_v1 *ls,
                         uint32_t serial, uint32_t w, uint32_t h) {
    (void)data;
    zwlr_layer_surface_v1_ack_configure(ls, serial);
    if (w) g_cfg_w = w;
    if (h) g_cfg_h = h;
    g_configured = true;
    fprintf(stderr, "[poc] layer_surface.configure serial=%u size=%ux%u (acked)\n",
            serial, w, h);
}
static void ls_closed(void *data, struct zwlr_layer_surface_v1 *ls) {
    (void)data; (void)ls;
    g_closed = true;
    fprintf(stderr, "[poc] layer_surface.closed (compositor dismissed us)\n");
}
static const struct zwlr_layer_surface_v1_listener ls_listener = {
    .configure = ls_configure,
    .closed = ls_closed,
};

static void reg_global(void *data, struct wl_registry *reg, uint32_t name,
                       const char *iface, uint32_t version) {
    (void)data;
    if (strcmp(iface, zwlr_layer_shell_v1_interface.name) == 0) {
        uint32_t v = version < 4 ? version : 4; /* v4 has the events we use */
        g_layer_shell = wl_registry_bind(reg, name,
                                         &zwlr_layer_shell_v1_interface, v);
        fprintf(stderr, "[poc] bound zwlr_layer_shell_v1 (v%u)\n", v);
    }
}
static void reg_global_remove(void *d, struct wl_registry *r, uint32_t n) {
    (void)d; (void)r; (void)n;
}
static const struct wl_registry_listener reg_listener = {
    .global = reg_global,
    .global_remove = reg_global_remove,
};

/* ----------------------------------------------------------------------------
 * Vulkan: minimal instance + swapchain over the wl_surface (clear-color only)
 * -------------------------------------------------------------------------- */
#define VKCHECK(call, msg) do { \
        VkResult _r = (call); \
        if (_r != VK_SUCCESS) { \
            fprintf(stderr, "[poc] VULKAN FAIL: %s (VkResult=%d)\n", msg, _r); \
            return false; \
        } \
    } while (0)

typedef struct {
    VkInstance       instance;
    VkPhysicalDevice phys;
    uint32_t         gfx_q_index;
    VkDevice         device;
    VkQueue          queue;
    VkSurfaceKHR     surface;
    VkSwapchainKHR   swapchain;
    VkFormat         fmt;
    VkExtent2D       extent;
    uint32_t         image_count;
    VkImage         *images;
    VkCommandPool    cmd_pool;
    VkCommandBuffer *cmd_bufs;
    VkSemaphore      sem_acquire;
    VkSemaphore      sem_release;
    VkFence          fence;
} VkState;

static bool vk_init_instance(VkState *vk) {
    /* SDL tells us exactly which instance extensions Wayland needs
       (VK_KHR_surface + VK_KHR_wayland_surface). */
    Uint32 ext_count = 0;
    char const *const *sdl_exts = SDL_Vulkan_GetInstanceExtensions(&ext_count);
    if (!sdl_exts) {
        fprintf(stderr, "[poc] SDL_Vulkan_GetInstanceExtensions failed: %s\n",
                SDL_GetError());
        return false;
    }
    fprintf(stderr, "[poc] SDL requests %u Vulkan instance extension(s):\n", ext_count);
    for (Uint32 i = 0; i < ext_count; i++)
        fprintf(stderr, "[poc]   - %s\n", sdl_exts[i]);

    VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "agentos-ue-poc",
        .apiVersion = VK_API_VERSION_1_2,
    };
    VkInstanceCreateInfo ci = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app,
        .enabledExtensionCount = ext_count,
        .ppEnabledExtensionNames = sdl_exts,
    };
    VKCHECK(vkCreateInstance(&ci, NULL, &vk->instance), "vkCreateInstance");
    return true;
}

static bool vk_pick_device(VkState *vk) {
    uint32_t n = 0;
    VKCHECK(vkEnumeratePhysicalDevices(vk->instance, &n, NULL),
            "enumerate phys (count)");
    if (!n) { fprintf(stderr, "[poc] no Vulkan physical devices\n"); return false; }
    VkPhysicalDevice *devs = calloc(n, sizeof(*devs));
    VKCHECK(vkEnumeratePhysicalDevices(vk->instance, &n, devs),
            "enumerate phys (list)");

    for (uint32_t i = 0; i < n && vk->phys == VK_NULL_HANDLE; i++) {
        uint32_t qn = 0;
        vkGetPhysicalDeviceQueueFamilyProperties(devs[i], &qn, NULL);
        VkQueueFamilyProperties *qp = calloc(qn, sizeof(*qp));
        vkGetPhysicalDeviceQueueFamilyProperties(devs[i], &qn, qp);
        for (uint32_t q = 0; q < qn; q++) {
            VkBool32 present = VK_FALSE;
            vkGetPhysicalDeviceSurfaceSupportKHR(devs[i], q, vk->surface, &present);
            if ((qp[q].queueFlags & VK_QUEUE_GRAPHICS_BIT) && present) {
                vk->phys = devs[i];
                vk->gfx_q_index = q;
                VkPhysicalDeviceProperties pp;
                vkGetPhysicalDeviceProperties(devs[i], &pp);
                fprintf(stderr, "[poc] picked GPU: %s (queue family %u)\n",
                        pp.deviceName, q);
                break;
            }
        }
        free(qp);
    }
    free(devs);
    if (vk->phys == VK_NULL_HANDLE) {
        fprintf(stderr, "[poc] no graphics+present queue found\n");
        return false;
    }
    return true;
}

static bool vk_create_device(VkState *vk) {
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = vk->gfx_q_index,
        .queueCount = 1,
        .pQueuePriorities = &prio,
    };
    const char *dev_exts[] = { VK_KHR_SWAPCHAIN_EXTENSION_NAME };
    VkDeviceCreateInfo dci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .queueCreateInfoCount = 1,
        .pQueueCreateInfos = &qci,
        .enabledExtensionCount = 1,
        .ppEnabledExtensionNames = dev_exts,
    };
    VKCHECK(vkCreateDevice(vk->phys, &dci, NULL, &vk->device), "vkCreateDevice");
    vkGetDeviceQueue(vk->device, vk->gfx_q_index, 0, &vk->queue);
    return true;
}

static bool vk_create_swapchain(VkState *vk, uint32_t want_w, uint32_t want_h) {
    VkSurfaceCapabilitiesKHR caps;
    VKCHECK(vkGetPhysicalDeviceSurfaceCapabilitiesKHR(vk->phys, vk->surface, &caps),
            "surface caps");

    /* extent: prefer compositor-fixed current extent, else our configured size */
    if (caps.currentExtent.width != 0xFFFFFFFFu) {
        vk->extent = caps.currentExtent;
    } else {
        vk->extent.width  = want_w ? want_w : 1920;
        vk->extent.height = want_h ? want_h : 1080;
    }

    uint32_t fn = 0;
    vkGetPhysicalDeviceSurfaceFormatsKHR(vk->phys, vk->surface, &fn, NULL);
    VkSurfaceFormatKHR *formats = calloc(fn, sizeof(*formats));
    vkGetPhysicalDeviceSurfaceFormatsKHR(vk->phys, vk->surface, &fn, formats);
    VkSurfaceFormatKHR chosen = formats[0];
    for (uint32_t i = 0; i < fn; i++) {
        if (formats[i].format == VK_FORMAT_B8G8R8A8_UNORM) { chosen = formats[i]; break; }
    }
    free(formats);
    vk->fmt = chosen.format;

    uint32_t min_imgs = caps.minImageCount + 1;
    if (caps.maxImageCount && min_imgs > caps.maxImageCount)
        min_imgs = caps.maxImageCount;

    VkSwapchainCreateInfoKHR sci = {
        .sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR,
        .surface = vk->surface,
        .minImageCount = min_imgs,
        .imageFormat = chosen.format,
        .imageColorSpace = chosen.colorSpace,
        .imageExtent = vk->extent,
        .imageArrayLayers = 1,
        .imageUsage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT |
                      VK_IMAGE_USAGE_TRANSFER_DST_BIT,
        .imageSharingMode = VK_SHARING_MODE_EXCLUSIVE,
        .preTransform = caps.currentTransform,
        .compositeAlpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR,
        .presentMode = VK_PRESENT_MODE_FIFO_KHR, /* always supported */
        .clipped = VK_TRUE,
    };
    VKCHECK(vkCreateSwapchainKHR(vk->device, &sci, NULL, &vk->swapchain),
            "vkCreateSwapchainKHR");

    VKCHECK(vkGetSwapchainImagesKHR(vk->device, vk->swapchain, &vk->image_count, NULL),
            "swapchain images (count)");
    vk->images = calloc(vk->image_count, sizeof(VkImage));
    VKCHECK(vkGetSwapchainImagesKHR(vk->device, vk->swapchain, &vk->image_count, vk->images),
            "swapchain images (list)");
    fprintf(stderr, "[poc] swapchain %ux%u fmt=%d images=%u (FIFO)\n",
            vk->extent.width, vk->extent.height, vk->fmt, vk->image_count);
    return true;
}

static bool vk_create_sync_and_cmds(VkState *vk) {
    VkCommandPoolCreateInfo pci = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT,
        .queueFamilyIndex = vk->gfx_q_index,
    };
    VKCHECK(vkCreateCommandPool(vk->device, &pci, NULL, &vk->cmd_pool), "cmd pool");

    vk->cmd_bufs = calloc(vk->image_count, sizeof(VkCommandBuffer));
    VkCommandBufferAllocateInfo ai = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = vk->cmd_pool,
        .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
        .commandBufferCount = vk->image_count,
    };
    VKCHECK(vkAllocateCommandBuffers(vk->device, &ai, vk->cmd_bufs), "alloc cmd bufs");

    VkSemaphoreCreateInfo sem = { .sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO };
    VKCHECK(vkCreateSemaphore(vk->device, &sem, NULL, &vk->sem_acquire), "sem acquire");
    VKCHECK(vkCreateSemaphore(vk->device, &sem, NULL, &vk->sem_release), "sem release");
    VkFenceCreateInfo fci = {
        .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO,
        .flags = VK_FENCE_CREATE_SIGNALED_BIT,
    };
    VKCHECK(vkCreateFence(vk->device, &fci, NULL, &vk->fence), "fence");
    return true;
}

/* Record a clear of one swapchain image to the test color, using a render-less
   image-layout transition + vkCmdClearColorImage (no pipeline/renderpass needed). */
static void vk_record_clear(VkState *vk, uint32_t idx, VkClearColorValue color) {
    VkCommandBuffer cb = vk->cmd_bufs[idx];
    vkResetCommandBuffer(cb, 0);
    VkCommandBufferBeginInfo bi = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
    };
    vkBeginCommandBuffer(cb, &bi);

    VkImageSubresourceRange range = {
        .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
        .levelCount = 1, .layerCount = 1,
    };

    /* UNDEFINED -> TRANSFER_DST for the clear */
    VkImageMemoryBarrier to_dst = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
        .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED,
        .newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
        .srcAccessMask = 0,
        .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
        .image = vk->images[idx],
        .subresourceRange = range,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
    };
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                         VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 0, NULL,
                         1, &to_dst);

    vkCmdClearColorImage(cb, vk->images[idx], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                         &color, 1, &range);

    /* TRANSFER_DST -> PRESENT_SRC */
    VkImageMemoryBarrier to_present = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
        .oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
        .newLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR,
        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
        .dstAccessMask = 0,
        .image = vk->images[idx],
        .subresourceRange = range,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
    };
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT,
                         VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, 0, 0, NULL, 0, NULL,
                         1, &to_present);

    vkEndCommandBuffer(cb);
}

static bool vk_draw_frame(VkState *vk, VkClearColorValue color) {
    vkWaitForFences(vk->device, 1, &vk->fence, VK_TRUE, UINT64_MAX);
    vkResetFences(vk->device, 1, &vk->fence);

    uint32_t idx = 0;
    VkResult acq = vkAcquireNextImageKHR(vk->device, vk->swapchain, UINT64_MAX,
                                         vk->sem_acquire, VK_NULL_HANDLE, &idx);
    if (acq == VK_ERROR_OUT_OF_DATE_KHR) return true; /* skip; would recreate */
    if (acq != VK_SUCCESS && acq != VK_SUBOPTIMAL_KHR) {
        fprintf(stderr, "[poc] acquire failed VkResult=%d\n", acq);
        return false;
    }

    vk_record_clear(vk, idx, color);

    VkPipelineStageFlags wait_stage = VK_PIPELINE_STAGE_TRANSFER_BIT;
    VkSubmitInfo si = {
        .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .waitSemaphoreCount = 1,
        .pWaitSemaphores = &vk->sem_acquire,
        .pWaitDstStageMask = &wait_stage,
        .commandBufferCount = 1,
        .pCommandBuffers = &vk->cmd_bufs[idx],
        .signalSemaphoreCount = 1,
        .pSignalSemaphores = &vk->sem_release,
    };
    if (vkQueueSubmit(vk->queue, 1, &si, vk->fence) != VK_SUCCESS) {
        fprintf(stderr, "[poc] queue submit failed\n");
        return false;
    }

    VkPresentInfoKHR pi = {
        .sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR,
        .waitSemaphoreCount = 1,
        .pWaitSemaphores = &vk->sem_release,
        .swapchainCount = 1,
        .pSwapchains = &vk->swapchain,
        .pImageIndices = &idx,
    };
    vkQueuePresentKHR(vk->queue, &pi);
    return true;
}

/* ----------------------------------------------------------------------------
 * main: SDL3 custom-role surface -> layer-shell BACKGROUND -> Vulkan loop
 * -------------------------------------------------------------------------- */
int main(void) {
    signal(SIGINT, on_sig);
    signal(SIGTERM, on_sig);
    setvbuf(stderr, NULL, _IONBF, 0);

    /* Force the Wayland video driver (we are proving the Wayland path). */
    SDL_SetHint(SDL_HINT_VIDEO_DRIVER, "wayland");
    if (!SDL_Init(SDL_INIT_VIDEO)) {
        fprintf(stderr, "[poc] FATAL: SDL_Init: %s\n", SDL_GetError());
        return 2;
    }
    const char *drv = SDL_GetCurrentVideoDriver();
    fprintf(stderr, "[poc] SDL video driver = %s\n", drv ? drv : "(null)");
    if (!drv || strcmp(drv, "wayland") != 0) {
        fprintf(stderr, "[poc] FATAL: SDL did not select the Wayland driver.\n");
        SDL_Quit();
        return 2;
    }

    /* --- create the window with the CUSTOM Wayland surface-role property ---
       This is the crux: SDL mints the wl_surface but does NOT give it an
       xdg_toplevel role, leaving us to assign one (exactly what UE rung-3 does). */
    SDL_PropertiesID props = SDL_CreateProperties();
    SDL_SetBooleanProperty(props,
        SDL_PROP_WINDOW_CREATE_WAYLAND_SURFACE_ROLE_CUSTOM_BOOLEAN, true);
    SDL_SetBooleanProperty(props, SDL_PROP_WINDOW_CREATE_VULKAN_BOOLEAN, true);
    /* size is a starting hint; the layer_surface.configure will give the real one */
    SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_WIDTH_NUMBER, 1920);
    SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_HEIGHT_NUMBER, 1080);

    SDL_Window *win = SDL_CreateWindowWithProperties(props);
    SDL_DestroyProperties(props);
    if (!win) {
        fprintf(stderr, "[poc] FATAL: SDL_CreateWindowWithProperties: %s\n",
                SDL_GetError());
        SDL_Quit();
        return 2;
    }
    fprintf(stderr, "[poc] SDL window created (custom Wayland role, Vulkan-capable)\n");

    /* --- pull the raw wl_display + wl_surface back out of the SDL window --- */
    SDL_PropertiesID wprops = SDL_GetWindowProperties(win);
    struct wl_display *display = SDL_GetPointerProperty(
        wprops, SDL_PROP_WINDOW_WAYLAND_DISPLAY_POINTER, NULL);
    struct wl_surface *surface = SDL_GetPointerProperty(
        wprops, SDL_PROP_WINDOW_WAYLAND_SURFACE_POINTER, NULL);
    if (!display || !surface) {
        fprintf(stderr, "[poc] FATAL: could not fetch wl_display=%p / wl_surface=%p\n",
                (void *)display, (void *)surface);
        SDL_DestroyWindow(win);
        SDL_Quit();
        return 3;
    }
    fprintf(stderr, "[poc] got raw wl_display=%p wl_surface=%p from SDL\n",
            (void *)display, (void *)surface);

    /* --- bind zwlr_layer_shell_v1 from the registry --- */
    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &reg_listener, NULL);
    wl_display_roundtrip(display);
    if (!g_layer_shell) {
        fprintf(stderr, "[poc] FATAL: zwlr_layer_shell_v1 not advertised by compositor\n");
        SDL_DestroyWindow(win);
        SDL_Quit();
        return 4;
    }

    /* --- assign the SDL-minted surface the BACKGROUND layer role --- */
    struct zwlr_layer_surface_v1 *ls = zwlr_layer_shell_v1_get_layer_surface(
        g_layer_shell, surface, NULL /* output: all/primary */,
        ZWLR_LAYER_SHELL_V1_LAYER_BACKGROUND, "agentos-ue-poc");
    zwlr_layer_surface_v1_add_listener(ls, &ls_listener, NULL);

    /* match the gtk4 stand-in exactly: 4-edge anchor, exclusive -1, kb none */
    zwlr_layer_surface_v1_set_anchor(ls,
        ZWLR_LAYER_SURFACE_V1_ANCHOR_TOP | ZWLR_LAYER_SURFACE_V1_ANCHOR_BOTTOM |
        ZWLR_LAYER_SURFACE_V1_ANCHOR_LEFT | ZWLR_LAYER_SURFACE_V1_ANCHOR_RIGHT);
    zwlr_layer_surface_v1_set_exclusive_zone(ls, -1);
    zwlr_layer_surface_v1_set_keyboard_interactivity(ls, 0);
    fprintf(stderr, "[poc] requested BACKGROUND role, anchor=4-edge, "
                    "exclusive_zone=-1, keyboard=none\n");

    /* IMPORTANT: an initial commit (no buffer) triggers the first configure. */
    wl_surface_commit(surface);

    /* pump until we get the configure (so we know the real surface size) */
    while (!g_configured && !g_closed && g_running) {
        if (wl_display_dispatch(display) < 0) break;
    }
    if (g_closed) {
        fprintf(stderr, "[poc] FATAL: compositor closed the layer surface "
                        "before configure\n");
        SDL_DestroyWindow(win);
        SDL_Quit();
        return 5;
    }
    fprintf(stderr, "[poc] configured surface size = %ux%u\n", g_cfg_w, g_cfg_h);

    /* --- Vulkan: instance / surface (over the SAME wl_surface) / swapchain --- */
    VkState vk; memset(&vk, 0, sizeof(vk));
    if (!vk_init_instance(&vk)) goto fail;

    /* SDL builds the VkSurfaceKHR from the very wl_surface we just gave the
       layer role to — proving Vulkan and layer-shell share one surface. */
    if (!SDL_Vulkan_CreateSurface(win, vk.instance, NULL, &vk.surface)) {
        fprintf(stderr, "[poc] FATAL: SDL_Vulkan_CreateSurface: %s\n", SDL_GetError());
        goto fail;
    }
    fprintf(stderr, "[poc] VkSurfaceKHR created over the layer-shell wl_surface\n");

    if (!vk_pick_device(&vk))   goto fail;
    if (!vk_create_device(&vk)) goto fail;
    if (!vk_create_swapchain(&vk, g_cfg_w, g_cfg_h)) goto fail;
    if (!vk_create_sync_and_cmds(&vk)) goto fail;

    fprintf(stderr, "[poc] ============================================\n");
    fprintf(stderr, "[poc] LIVE: Vulkan clear-color wallpaper running.\n");
    fprintf(stderr, "[poc] color = dark green #0b3618 (the test fill)\n");
    fprintf(stderr, "[poc] In a 2nd shell run: ../wallpaper_role/dump_stacking.sh\n");
    fprintf(stderr, "[poc] PASS = this fill at stacking index [1] (above plasma\n");
    fprintf(stderr, "[poc]        desktop containment [0], below panel + windows).\n");
    fprintf(stderr, "[poc] Stop: Ctrl-C or  pkill -f '[s]dl3_vulkan_wallpaper'\n");
    fprintf(stderr, "[poc] ============================================\n");

    VkClearColorValue color = { .float32 = { CLEAR_R, CLEAR_G, CLEAR_B, 1.0f } };

    /* render loop: live wallpaper behavior, FIFO-paced (~vsync) */
    while (g_running && !g_closed) {
        /* keep the wl event queue moving (handle resize/close); non-blocking */
        wl_display_dispatch_pending(display);
        wl_display_flush(display);

        SDL_Event ev;
        while (SDL_PollEvent(&ev)) {
            if (ev.type == SDL_EVENT_QUIT) g_running = 0;
        }

        if (!vk_draw_frame(&vk, color)) break;
    }

    fprintf(stderr, "\n[poc] tearing down.\n");
    vkDeviceWaitIdle(vk.device);

    /* teardown (best-effort; process exit would reclaim anyway) */
    if (vk.fence)       vkDestroyFence(vk.device, vk.fence, NULL);
    if (vk.sem_acquire) vkDestroySemaphore(vk.device, vk.sem_acquire, NULL);
    if (vk.sem_release) vkDestroySemaphore(vk.device, vk.sem_release, NULL);
    if (vk.cmd_pool)    vkDestroyCommandPool(vk.device, vk.cmd_pool, NULL);
    free(vk.cmd_bufs);
    free(vk.images);
    if (vk.swapchain)   vkDestroySwapchainKHR(vk.device, vk.swapchain, NULL);
    if (vk.device)      vkDestroyDevice(vk.device, NULL);
    if (vk.surface)     vkDestroySurfaceKHR(vk.instance, vk.surface, NULL);
    if (vk.instance)    vkDestroyInstance(vk.instance, NULL);

    zwlr_layer_surface_v1_destroy(ls);
    SDL_DestroyWindow(win);
    SDL_Quit();
    return 0;

fail:
    fprintf(stderr, "[poc] aborting after Vulkan setup failure.\n");
    if (vk.surface)  vkDestroySurfaceKHR(vk.instance, vk.surface, NULL);
    if (vk.instance) vkDestroyInstance(vk.instance, NULL);
    SDL_DestroyWindow(win);
    SDL_Quit();
    return 6;
}
