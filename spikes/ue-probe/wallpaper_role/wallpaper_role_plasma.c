/* wallpaper_role_plasma.c — ADR-0029 §A PoC-0b (plasma Desktop-role gate).
 *
 * QUESTION: will KWin (Plasma 6) host a FOREIGN, RAW wl_surface as the wallpaper
 * when we request the org_kde_plasma_surface DESKTOP role — full-screen, BEHIND
 * the panel, BEHIND app windows, and ideally BEHIND the desktop icons?
 *
 * Why raw-Wayland (not GTK/Qt): the real integration injects the chosen role onto
 * UE's window via SDL3's SDL_PROP_WINDOW_CREATE_WAYLAND_SURFACE_ROLE_CUSTOM_BOOLEAN,
 * which hands you a bare wl_surface to drive yourself. gtk4-layer-shell (PoC-0a) can
 * ONLY speak zwlr_layer_shell and composited ABOVE the desktop icons. The plasma
 * "desktop" role is the slot Plasma's own desktopcontainment uses, so it is the
 * candidate that should sit BELOW the icons. This client validates exactly that role
 * on a bare wl_surface — the same surface shape SDL3 will give UE.
 *
 * GPU-trivial: software wl_shm fill only. NO Vulkan, NO EGL, NO GPU memory. ~tens of MB.
 *
 * Protocol ground truth (plasma-shell.xml, org_kde_plasma_surface v8):
 *   role enum:  normal=0, DESKTOP=1, panel=2, onscreendisplay=3, ...
 *   "Desktop surfaces are placed below all other surfaces ... Only one surface per
 *    output can have the desktop role."  <-- precisely the wallpaper contract.
 *
 * SAFETY: paints a clearly-labelled distinct dark-teal fill (#0b362a) so it reads as
 * an obvious test, never as a glitch. Press Ctrl-C (or `pkill -f '[w]allpaper_role_plasma'`)
 * to stop and the surface vanishes; nothing persists.
 */

#define _GNU_SOURCE /* memfd_create, mkostemp */
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include <wayland-client.h>
#include "plasma-shell-client-protocol.h"

/* ---- distinct test fill: dark teal #0b362a, ARGB8888 (premultiplied-ish, opaque) ---- */
#define FILL_ARGB 0xff0b362a
/* a thin bright border so the surface extent is unmistakable on screen */
#define BORDER_ARGB 0xff2fd0a8
#define BORDER_PX 8

static struct wl_compositor *compositor = NULL;
static struct wl_shm *shm = NULL;
static struct org_kde_plasma_shell *plasma_shell = NULL;
static struct wl_output *output = NULL;

static int32_t out_w = 0, out_h = 0; /* filled from wl_output.mode (physical px) */
static volatile sig_atomic_t running = 1;

static void on_sigint(int sig) {
    (void)sig;
    running = 0;
}

/* ----------------------------- wl_output listener ----------------------------- */
static void output_geometry(void *d, struct wl_output *o, int32_t x, int32_t y,
                            int32_t pw, int32_t ph, int32_t sub, const char *make,
                            const char *model, int32_t transform) {
    (void)d; (void)o; (void)x; (void)y; (void)pw; (void)ph; (void)sub;
    (void)make; (void)model; (void)transform;
}
static void output_mode(void *d, struct wl_output *o, uint32_t flags,
                        int32_t w, int32_t h, int32_t refresh) {
    (void)d; (void)o; (void)refresh;
    if (flags & WL_OUTPUT_MODE_CURRENT) {
        out_w = w;
        out_h = h;
    }
}
static void output_done(void *d, struct wl_output *o) { (void)d; (void)o; }
static void output_scale(void *d, struct wl_output *o, int32_t s) { (void)d; (void)o; (void)s; }
static void output_name(void *d, struct wl_output *o, const char *n) { (void)d; (void)o; (void)n; }
static void output_description(void *d, struct wl_output *o, const char *desc) {
    (void)d; (void)o; (void)desc;
}
static const struct wl_output_listener output_listener = {
    .geometry = output_geometry,
    .mode = output_mode,
    .done = output_done,
    .scale = output_scale,
    .name = output_name,
    .description = output_description,
};

/* ----------------------------- registry listener ------------------------------ */
static void reg_global(void *data, struct wl_registry *reg, uint32_t name,
                       const char *iface, uint32_t version) {
    (void)data;
    if (strcmp(iface, wl_compositor_interface.name) == 0) {
        compositor = wl_registry_bind(reg, name, &wl_compositor_interface,
                                      version < 4 ? version : 4);
    } else if (strcmp(iface, wl_shm_interface.name) == 0) {
        shm = wl_registry_bind(reg, name, &wl_shm_interface, 1);
    } else if (strcmp(iface, org_kde_plasma_shell_interface.name) == 0) {
        /* bind at the version the compositor advertises (v8 here) */
        plasma_shell = wl_registry_bind(reg, name, &org_kde_plasma_shell_interface,
                                        version < 8 ? version : 8);
    } else if (strcmp(iface, wl_output_interface.name) == 0) {
        if (!output) { /* first/primary output only */
            output = wl_registry_bind(reg, name, &wl_output_interface,
                                      version < 4 ? version : 4);
            wl_output_add_listener(output, &output_listener, NULL);
        }
    }
}
static void reg_global_remove(void *d, struct wl_registry *r, uint32_t n) {
    (void)d; (void)r; (void)n;
}
static const struct wl_registry_listener registry_listener = {
    .global = reg_global,
    .global_remove = reg_global_remove,
};

/* ----------------------------- shm buffer helper ------------------------------ */
static int create_anon_file(size_t size) {
    char tmpl[] = "/wallpaper_role-XXXXXX";
    int fd = -1;
#ifdef __linux__
    fd = memfd_create("wallpaper_role_shm", MFD_CLOEXEC);
#endif
    if (fd < 0) {
        char path[64];
        const char *xdg = getenv("XDG_RUNTIME_DIR");
        snprintf(path, sizeof(path), "%s%s", xdg ? xdg : "/tmp", tmpl);
        fd = mkostemp(path, O_CLOEXEC);
        if (fd >= 0) unlink(path);
    }
    if (fd < 0) return -1;
    if (ftruncate(fd, (off_t)size) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static struct wl_buffer *make_buffer(int32_t w, int32_t h) {
    int stride = w * 4;
    size_t size = (size_t)stride * h;
    int fd = create_anon_file(size);
    if (fd < 0) {
        fprintf(stderr, "[poc] shm alloc failed: %s\n", strerror(errno));
        return NULL;
    }
    uint32_t *px = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (px == MAP_FAILED) {
        fprintf(stderr, "[poc] mmap failed: %s\n", strerror(errno));
        close(fd);
        return NULL;
    }
    for (int32_t y = 0; y < h; y++) {
        for (int32_t x = 0; x < w; x++) {
            int border = (x < BORDER_PX || x >= w - BORDER_PX ||
                          y < BORDER_PX || y >= h - BORDER_PX);
            px[y * w + x] = border ? BORDER_ARGB : FILL_ARGB;
        }
    }
    struct wl_shm_pool *pool = wl_shm_create_pool(shm, fd, size);
    struct wl_buffer *buf =
        wl_shm_pool_create_buffer(pool, 0, w, h, stride, WL_SHM_FORMAT_ARGB8888);
    wl_shm_pool_destroy(pool);
    munmap(px, size);
    close(fd);
    return buf;
}

int main(void) {
    signal(SIGINT, on_sigint);
    signal(SIGTERM, on_sigint);
    setvbuf(stdout, NULL, _IONBF, 0);

    struct wl_display *display = wl_display_connect(NULL);
    if (!display) {
        fprintf(stderr, "[poc] FATAL: cannot connect to Wayland display "
                        "(WAYLAND_DISPLAY=%s)\n", getenv("WAYLAND_DISPLAY"));
        return 2;
    }
    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    wl_display_roundtrip(display); /* bind globals */
    wl_display_roundtrip(display); /* drain wl_output.mode events */

    if (!compositor || !shm || !plasma_shell) {
        fprintf(stderr, "[poc] FATAL: missing globals — compositor=%p shm=%p "
                        "org_kde_plasma_shell=%p\n",
                (void *)compositor, (void *)shm, (void *)plasma_shell);
        fprintf(stderr, "[poc] (org_kde_plasma_shell absent ⇒ not a Plasma session)\n");
        return 3;
    }
    printf("[poc] bound: wl_compositor, wl_shm, org_kde_plasma_shell (v8)\n");

    int32_t w = out_w > 0 ? out_w : 1920;
    int32_t h = out_h > 0 ? out_h : 1080;
    printf("[poc] target surface size: %dx%d (from wl_output mode: %s)\n",
           w, h, (out_w > 0) ? "yes" : "FALLBACK 1920x1080");

    /* 1) bare wl_surface — exactly the shape SDL3 hands you for UE */
    struct wl_surface *surface = wl_compositor_create_surface(compositor);

    /* 2) promote it to a plasma shell surface and request the DESKTOP role */
    struct org_kde_plasma_surface *psurf =
        org_kde_plasma_shell_get_surface(plasma_shell, surface);
    org_kde_plasma_surface_set_role(psurf, ORG_KDE_PLASMA_SURFACE_ROLE_DESKTOP); /* = 1 */
    printf("[poc] requested org_kde_plasma_surface.set_role(DESKTOP=%d)\n",
           ORG_KDE_PLASMA_SURFACE_ROLE_DESKTOP);

    /* keep it out of the taskbar / alt-tab switcher; pin to 0,0 of the output */
    org_kde_plasma_surface_set_skip_taskbar(psurf, 1);
    org_kde_plasma_surface_set_skip_switcher(psurf, 1);
    if (output) org_kde_plasma_surface_set_output(psurf, output);
    org_kde_plasma_surface_set_position(psurf, 0, 0);
    printf("[poc] set skip_taskbar=1, skip_switcher=1, position=0,0\n");

    /* 3) paint + commit the software fill */
    struct wl_buffer *buffer = make_buffer(w, h);
    if (!buffer) {
        wl_display_disconnect(display);
        return 4;
    }
    wl_surface_attach(surface, buffer, 0, 0);
    wl_surface_damage_buffer(surface, 0, 0, w, h);
    wl_surface_commit(surface);
    wl_display_roundtrip(display);

    printf("[poc] PRESENTED desktop-role surface (dark-teal #0b362a, %dpx teal border).\n", BORDER_PX);
    printf("[poc] If KWin accepted it, this fill is now the wallpaper layer.\n");
    printf("[poc] --> NOW run the stacking dump:  ./dump_stacking.sh\n");
    printf("[poc] PASS = this surface is BELOW the panel + app windows (ideally below desktop icons).\n");
    printf("[poc] Ctrl-C (or pkill -f '[w]allpaper_role_plasma') to remove it.\n");

    while (running && wl_display_dispatch(display) != -1) {
        /* stay mapped; static fill, no redraw loop */
    }

    printf("\n[poc] tearing down.\n");
    org_kde_plasma_surface_destroy(psurf); /* protocol: destroy plasma_surface before wl_surface */
    wl_surface_destroy(surface);
    wl_buffer_destroy(buffer);
    wl_display_disconnect(display);
    return 0;
}
