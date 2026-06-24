// agentos_layershell.c — ADR-0029 §A rung 3 helper.
//
// Assigns the zwlr_layer_shell_v1 BACKGROUND role to a FOREIGN wl_surface (UE's
// SDL3-minted custom-role surface), turning the UE window into the desktop
// wallpaper. Built as libagentos_layershell.so and dlopen'd by the LinuxWindow.cpp
// patch (env-gated by AGENTOS_WALLPAPER). ALL wayland/layer-shell complexity lives
// here so the UE engine diff stays ~15 lines with no wayland-client dependency.
//
// Sequencing (why this is safe inside UE): UE creates the window HIDDEN with the
// custom property UE.window.present_enabled=false, and VulkanRHI honors that gate
// (VulkanLinuxPlatform.cpp:388) — so NO Vulkan buffer is committed before we assign
// the role. We perform the first (empty) wl_surface_commit here to elicit the
// layer_surface.configure, ack it, and return; UE's Show() later flips
// present_enabled=true and Vulkan presents into the already-configured layer surface.
// This mirrors the rung-2 PoC (sdl3_vulkan_wallpaper.c) which proved a single
// SDL3 custom-role wl_surface can carry both the BACKGROUND role and a live Vulkan
// swapchain on this KWin 6.6.5 box.
//
// We share UE/SDL's wl_display. Our setup roundtrip runs during window creation,
// before UE enters its SDL event pump, so it does not race SDL's dispatch; future
// configures (e.g. output change) are delivered to our heap-allocated listener via
// SDL's normal default-queue dispatch.

#include <wayland-client.h>
#include "wlr-layer-shell-unstable-v1-client-protocol.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

struct agentos_ls {
	struct zwlr_layer_shell_v1   *shell;
	struct zwlr_layer_surface_v1 *surface;
	int       configured;
	uint32_t  w, h;
};

static void on_global(void *data, struct wl_registry *reg, uint32_t name,
                      const char *iface, uint32_t version) {
	struct agentos_ls *s = (struct agentos_ls *)data;
	if (strcmp(iface, zwlr_layer_shell_v1_interface.name) == 0) {
		uint32_t v = version < 4u ? version : 4u;       // rung-2 proved v4 here
		s->shell = (struct zwlr_layer_shell_v1 *)
			wl_registry_bind(reg, name, &zwlr_layer_shell_v1_interface, v);
	}
}
static void on_global_remove(void *data, struct wl_registry *reg, uint32_t name) {
	(void)data; (void)reg; (void)name;
}
static const struct wl_registry_listener registry_listener = { on_global, on_global_remove };

static void on_configure(void *data, struct zwlr_layer_surface_v1 *ls,
                         uint32_t serial, uint32_t w, uint32_t h) {
	struct agentos_ls *s = (struct agentos_ls *)data;
	zwlr_layer_surface_v1_ack_configure(ls, serial);
	s->w = w; s->h = h; s->configured = 1;
}
static void on_closed(void *data, struct zwlr_layer_surface_v1 *ls) {
	(void)data; (void)ls;
}
static const struct zwlr_layer_surface_v1_listener layer_surface_listener = {
	on_configure, on_closed,
};

// Assign the BACKGROUND wallpaper role to `surface` on connection `display`.
// Returns 0 on success (surface configured), nonzero on failure. Call once.
// Symbol is C-linkage and default-visibility for dlsym().
__attribute__((visibility("default")))
int agentos_layershell_background(struct wl_display *display, struct wl_surface *surface) {
	if (!display || !surface) return 1;

	// Heap-allocated so the listener's data pointer outlives this call (process-lifetime).
	struct agentos_ls *s = (struct agentos_ls *)calloc(1, sizeof(*s));
	if (!s) return 2;

	struct wl_registry *registry = wl_display_get_registry(display);
	wl_registry_add_listener(registry, &registry_listener, s);
	if (wl_display_roundtrip(display) < 0) {
		fprintf(stderr, "[agentos-ls] registry roundtrip failed\n"); return 3;
	}
	if (!s->shell) {
		fprintf(stderr, "[agentos-ls] zwlr_layer_shell_v1 not advertised\n"); return 4;
	}

	s->surface = zwlr_layer_shell_v1_get_layer_surface(
		s->shell, surface, NULL /* compositor picks the output */,
		ZWLR_LAYER_SHELL_V1_LAYER_BACKGROUND, "agentos-ue-wallpaper");
	if (!s->surface) {
		fprintf(stderr, "[agentos-ls] get_layer_surface failed\n"); return 5;
	}
	zwlr_layer_surface_v1_add_listener(s->surface, &layer_surface_listener, s);
	zwlr_layer_surface_v1_set_anchor(s->surface,
		ZWLR_LAYER_SURFACE_V1_ANCHOR_TOP    | ZWLR_LAYER_SURFACE_V1_ANCHOR_BOTTOM |
		ZWLR_LAYER_SURFACE_V1_ANCHOR_LEFT   | ZWLR_LAYER_SURFACE_V1_ANCHOR_RIGHT);
	zwlr_layer_surface_v1_set_exclusive_zone(s->surface, -1);          // ignore panel reservations
	zwlr_layer_surface_v1_set_keyboard_interactivity(s->surface, 0);   // input-less, like a wallpaper
	wl_surface_commit(surface);                                        // first (empty) commit → configure

	int guard = 0;
	while (!s->configured && guard++ < 200) {
		if (wl_display_roundtrip(display) < 0) {
			fprintf(stderr, "[agentos-ls] configure roundtrip failed\n"); return 6;
		}
	}
	if (!s->configured) {
		fprintf(stderr, "[agentos-ls] no configure received\n"); return 7;
	}

	fprintf(stderr, "[agentos-ls] BACKGROUND role assigned; configured %ux%u\n", s->w, s->h);
	wl_registry_destroy(registry);   // shell/surface proxies persist (process-lifetime)
	return 0;
}
