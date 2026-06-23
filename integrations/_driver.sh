#!/usr/bin/env bash
# Shared driver for the AgentOS component installer. Sourced by install.sh (MODE=install) and
# uninstall.sh (MODE=uninstall). Reads components.conf and apply/restores the SELECTED components.
# Stays user-scope: privileged (sudo) and manual steps are PRINTED at the end, never auto-escalated.
# A component that fails logs and the run CONTINUES (no half-applied abort).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="$HERE/components.conf"
MODE="${MODE:-install}"
[ -f "$REGISTRY" ] || { echo "✗ registry not found: $REGISTRY" >&2; exit 1; }

trim() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"; printf '%s' "$s"; }

declare -a C_ID C_TIER C_DEF C_ROOT C_APPLY C_RESTORE C_DESC
while IFS='|' read -r f_id f_tier f_def f_root f_apply f_restore f_desc; do
  f_id="$(trim "${f_id:-}")"
  [ -z "$f_id" ] && continue
  [ "${f_id:0:1}" = "#" ] && continue
  C_ID+=("$f_id");            C_TIER+=("$(trim "${f_tier:-}")")
  C_DEF+=("$(trim "${f_def:-}")");  C_ROOT+=("$(trim "${f_root:-}")")
  C_APPLY+=("$(trim "${f_apply:-}")"); C_RESTORE+=("$(trim "${f_restore:-}")")
  C_DESC+=("$(trim "${f_desc:-}")")
done < "$REGISTRY"
N=${#C_ID[@]}
[ "$N" -gt 0 ] || { echo "✗ no components parsed from $REGISTRY" >&2; exit 1; }

idx_of() { local id="$1" i; for i in "${!C_ID[@]}"; do [ "${C_ID[$i]}" = "$id" ] && { echo "$i"; return 0; }; done; return 1; }

usage() {
  local verb=install; [ "$MODE" = uninstall ] && verb=uninstall
  cat <<EOF
AgentOS component ${verb}er — reads components.conf and ${verb}s the selected components.
Stays user-scope; privileged/manual steps are PRINTED at the end (never auto-sudo).

usage: ${0##*/} [--list] [--all] [--defaults] [--only a,b] [--without a,b] [--yes]
  --list         explain the architecture + the registry grouped by how each part ties in
  --all          select every component
  --defaults     select the default-on set (non-interactive)
  --only a,b     select exactly these ids
  --without a,b  the default-on set minus these ids
  --yes,-y       non-interactive; with no selector, implies --defaults
  --preflight    check what the selected (or default) components ASSUME is on the box, then exit
  --onboard [..] guided model setup (ADR-0044): detect what's here, fetch only the gaps.
                 e.g. --onboard detect | --onboard fetch image --yes | --onboard creds set civitai
  (no args on a terminal) → interactive checklist
EOF
}

# How each component ties into AgentOS. The substrate (core) is what nothing else does;
# every other tier is a CONSUMER of it. Printed by --list and atop the interactive checklist
# so the registry never reads as a flat bag of opaque ids. ADR-0001 + CLAUDE.md relationship map.
arch_banner() {
  cat <<'EOF'
AgentOS is a resource+safety SUBSTRATE — not an OS, a distro, or an orchestrator.
  • the orchestrator is Hermes (~/.hermes); the desktop is CachyOS + the Nimbus pack.
  • AgentOS is the floor under both: it owns the GPU VRAM lease and the reversible
    apply/rollback tx — and everything below is a consumer of it.

    Hermes  ─inference──────────▶ agentosd proxy ──▶ Ollama (:11434)
    Hermes  ─D-Bus lease/priority▶ agentosd ──NVML read + evict──▶ GPU
    Desktop ─keyhole · wallpaper · theme · right-click──▶ consumes agentosd

Components are grouped by how they tie in:
EOF
}

# One line per tier: its role in the system, so the grouping itself explains the wiring.
tier_role() {
  case "$1" in
    core)       echo "the substrate itself — agentosd: the VRAM lease/coordinator, the read-only feed/keyhole/telemetry producers, the apply/rollback tx. Everything else consumes this." ;;
    service)    echo "local daemons & web surfaces that sit beside or consume the substrate (all bound to 127.0.0.1)." ;;
    desktop)    echo "Plasma 6 / KWin integrations — how AgentOS shows up on the desktop: tray, wallpaper reactivity, theming, right-click." ;;
    hermes)     echo "glue into the Hermes orchestrator — plugs the agentosd VRAM lease into Hermes inference. AgentOS does not replace Hermes." ;;
    remote)     echo "network exposure (tailnet-only). Off by default; printed for you to run, never auto-escalated." ;;
    privileged) echo "the steps that need root. Always printed for you to run, never auto-sudo'd." ;;
    *)          echo "other components." ;;
  esac
}

# Conceptual order: substrate first, then outward to its consumers.
TIER_ORDER=(core service desktop hermes remote privileged)

list_table() {
  arch_banner
  # ordered tiers that actually appear, then any unknown tiers so nothing is silently hidden
  local order=() seen_t="" t i
  for t in "${TIER_ORDER[@]}"; do
    for ((i=0;i<N;i++)); do [ "${C_TIER[$i]}" = "$t" ] && { order+=("$t"); seen_t="$seen_t $t "; break; }; done
  done
  for ((i=0;i<N;i++)); do
    case "$seen_t" in *" ${C_TIER[$i]} "*) : ;; *) order+=("${C_TIER[$i]}"); seen_t="$seen_t ${C_TIER[$i]} " ;; esac
  done
  for t in "${order[@]}"; do
    printf '\n%s — %s\n' "${t^^}" "$(tier_role "$t")"
    printf '  %-18s %-8s %-7s %s\n' ID DEFAULT ROOT DESCRIPTION
    for ((i=0;i<N;i++)); do
      [ "${C_TIER[$i]}" = "$t" ] || continue
      printf '  %-18s %-8s %-7s %s\n' "${C_ID[$i]}" "${C_DEF[$i]}" "${C_ROOT[$i]}" "${C_DESC[$i]}"
    done
  done
}

# ── preflight: surface what the SELECTED components ASSUME is already on the box ──────────────
# AgentOS coordinates heavy neighbors rather than bundling them (ADR-0001), so the installer can't
# "grab" Ollama/Hermes/ComfyUI/weights — but it CAN check them and say so, instead of letting a
# component fail opaquely later. Read-only, never aborts (honors fail-open + continue-on-failure).
_sel()  { [ -n "${SEL[$1]:-}" ]; }                      # is component $1 selected?
_anysel() { local id; for id in "$@"; do _sel "$id" && return 0; done; return 1; }
PREFLIGHT_MISS=0
_pf() {  # _pf "label" <0-if-present|1-if-missing> "hint when missing"
  if [ "$2" -eq 0 ]; then printf '  ✓ %s\n' "$1"
  else printf '  ✗ %s — %s\n' "$1" "$3"; PREFLIGHT_MISS=$((PREFLIGHT_MISS+1)); fi
}
preflight() {
  echo "Preflight — what the selected components assume is already present (read-only; nothing is changed):"
  # core-substrate compiles the Rust binary every other unit ExecStarts
  if _sel core-substrate; then
    command -v cargo >/dev/null 2>&1; _pf "Rust toolchain (cargo) — builds agentosd" $? "install rustup → https://rustup.rs"
  fi
  # the lucid/share/drain services lazy-import Pillow + numpy at runtime
  if _anysel lucid share-hub lucid-drain; then
    python3 -c "import PIL, numpy" >/dev/null 2>&1; _pf "Python Pillow + numpy — Lucid/Share image ops" $? "pacman -S python-pillow python-numpy"
  fi
  # the Hermes-coupled components need the orchestrator install
  if _anysel hermes-dashboard hermes-plugins; then
    { [ -d "$HOME/.hermes" ]; }; _pf "Hermes orchestrator (~/.hermes)" $? "install Hermes Agent first (it is the orchestrator; AgentOS is the substrate under it)"
  fi
  # the gpu-coordinator plugin prefers the pure-python persistent D-Bus transport; degrades to busctl
  if _sel hermes-plugins; then
    python3 -c "import jeepney" >/dev/null 2>&1; _pf "Python jeepney — gpu-coordinator persistent lease transport (else busctl fallback)" $? "pip install --user jeepney  (optional; cooperative-lease churns without it)"
  fi
  # ComfyUI runtime (its own venv) is the dreaming backend
  if _anysel comfyui lucid; then
    { [ -x "$HOME/ComfyUI/.venv/bin/python" ]; }; _pf "ComfyUI runtime (~/ComfyUI/.venv)" $? "clone + venv ComfyUI (dreams fail-open to the shader without it)"
    if [ -d "$HOME/ComfyUI/models" ] && [ -n "$(find "$HOME/ComfyUI/models" -type f -size +100M -print -quit 2>/dev/null)" ]; then
      _pf "ComfyUI model weights present (~/ComfyUI/models)" 0 ""
    else
      _pf "ComfyUI model weights (~/ComfyUI/models)" 1 "download the dreaming weights; jobs error at run-time without them"
    fi
  fi
  # Ollama is the model runtime; the panels/inference reach it over HTTP and fail soft
  if _anysel lucid models-panel; then
    command -v ollama >/dev/null 2>&1; _pf "Ollama model runtime (:11434)" $? "install ollama (panels/inference fail soft without it)"
  fi
  if [ "$PREFLIGHT_MISS" -gt 0 ]; then
    echo "  → $PREFLIGHT_MISS assumption(s) unmet above. AgentOS does not bundle these by design; install them"
    echo "    and re-run, or proceed — the affected component fails open/honestly, it won't wedge the rest."
  fi
  echo
}

# ── linger: --user services die at logout/reboot unless the user has linger enabled ──────────
# Enabling linger for your OWN user needs no root on a standard logind, so it fits the user-scope
# contract. Fail-soft to a printed manual step (matches the sudo/manual print path).
ensure_linger() {
  [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo no)" = yes ] && return 0
  if loginctl enable-linger "$USER" 2>/dev/null; then
    echo "✓ enabled linger for $USER — --user services now survive logout/reboot"; echo
  else
    PRINTS+=("loginctl enable-linger $USER   # so AgentOS --user services survive logout/reboot")
  fi
}

declare -A SEL=()   # =() so ${#SEL[@]} is safe under set -u even when nothing is selected yet
sel_defaults() { local i; for ((i=0;i<N;i++)); do [ "${C_DEF[$i]}" = on ] && SEL["${C_ID[$i]}"]=1; done; }
sel_all()      { local i; for ((i=0;i<N;i++)); do SEL["${C_ID[$i]}"]=1; done; }
sel_csv()      { local id; IFS=',' read -ra a <<<"$1"; for id in "${a[@]}"; do id="$(trim "$id")"; [ -z "$id" ] && continue; idx_of "$id" >/dev/null || { echo "✗ unknown component: $id (see --list)" >&2; exit 2; }; SEL["$id"]=1; done; }
desel_csv()    { local id; IFS=',' read -ra a <<<"$1"; for id in "${a[@]}"; do id="$(trim "$id")"; [ -z "$id" ] && continue; idx_of "$id" >/dev/null || { echo "✗ unknown component: $id (see --list)" >&2; exit 2; }; unset "SEL[$id]" 2>/dev/null || true; done; }

interactive() {
  if command -v whiptail >/dev/null 2>&1; then
    local args=() i st chosen id
    # prefix each row with its [tier] so the wiring (substrate vs consumer) shows in the flat checklist
    for ((i=0;i<N;i++)); do st=OFF; [ "${C_DEF[$i]}" = on ] && st=ON; args+=("${C_ID[$i]}" "[${C_TIER[$i]}] ${C_DESC[$i]:0:40}" "$st"); done
    chosen="$(whiptail --title "AgentOS components — $MODE" --checklist \
      "AgentOS is a VRAM/safety substrate under Hermes + the Nimbus desktop; each row is a part you wire in ([tier] = how it ties in — run --list for the map).\nSpace toggles • Enter confirms • default-on preselected" 22 88 13 "${args[@]}" 3>&1 1>&2 2>&3)" \
      || { echo "cancelled."; exit 0; }
    for id in $chosen; do id="${id//\"/}"; SEL["$id"]=1; done
  else
    echo "(whiptail not found — proceeding with the default set; pass --only/--without to customise.)"
    list_table; echo
    sel_defaults
    local shown="" i; for ((i=0;i<N;i++)); do [ -n "${SEL[${C_ID[$i]}]:-}" ] && shown="$shown ${C_ID[$i]}"; done
    read -rp "${MODE^} defaults [$shown ]? [Y/n] or type space-separated ids: " ans || ans=""
    case "$ans" in
      ""|[Yy]*) : ;;
      [Nn]*) echo "nothing selected."; exit 0 ;;
      *) SEL=(); for id in $ans; do idx_of "$id" >/dev/null || { echo "✗ unknown: $id" >&2; exit 2; }; SEL["$id"]=1; done ;;
    esac
  fi
}

SELECTOR="" ONLY="" WITHOUT="" YES=0 PREFLIGHT_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --list) list_table; exit 0 ;;
    --onboard) shift; exec python3 "$HERE/setup/setup.py" "$@" ;;   # ADR-0044 model onboarding
    --all) SELECTOR=all ;;
    --defaults) SELECTOR=defaults ;;
    --only) shift; ONLY="${1:-}"; SELECTOR=only ;;
    --without) shift; WITHOUT="${1:-}"; SELECTOR=without ;;
    --yes|-y) YES=1 ;;
    --preflight) PREFLIGHT_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

case "$SELECTOR" in
  all)      sel_all ;;
  defaults) sel_defaults ;;
  only)     sel_csv "$ONLY" ;;
  without)  sel_defaults; desel_csv "$WITHOUT" ;;
  "")       if [ "$PREFLIGHT_ONLY" = 1 ] || [ "$YES" = 1 ] || [ ! -t 0 ]; then sel_defaults; else interactive; fi ;;
esac
[ "${#SEL[@]}" -gt 0 ] || { echo "nothing selected — see --list."; exit 0; }

# --preflight: just report the assumptions for the selected set and stop (never installs/changes anything)
[ "$PREFLIGHT_ONLY" = 1 ] && { preflight; exit 0; }

verb=apply; [ "$MODE" = uninstall ] && verb=restore
echo "→ ${MODE}: ${!SEL[*]}"
echo "  (AgentOS = a VRAM/safety substrate + its consumers; run './${0##*/} --list' for how each part ties in)"
echo
PRINTS=()   # =() (not `declare -a`) so an empty array is "set" for set -u when nothing is printed
if [ "$MODE" = install ]; then
  preflight                                            # surface unmet neighbor/dep assumptions up front
  # linger only matters when we install --user units (core/service tiers)
  for i in "${!C_ID[@]}"; do _sel "${C_ID[$i]}" && case "${C_TIER[$i]}" in core|service) ensure_linger; break ;; esac; done
fi
seq_order=$(seq 0 $((N-1))); [ "$MODE" = uninstall ] && seq_order=$(seq $((N-1)) -1 0)
for i in $seq_order; do
  id="${C_ID[$i]}"; [ -n "${SEL[$id]:-}" ] || continue
  cmd="${C_APPLY[$i]}"; [ "$MODE" = uninstall ] && cmd="${C_RESTORE[$i]}"
  case "${C_ROOT[$i]}" in
    no)
      [ -n "$cmd" ] || { echo "! $id: empty $verb command in components.conf — skipping"; echo; continue; }
      echo "━━ $id ━━"
      if ( cd "$HERE" && eval "./$cmd" ); then crc=0; else crc=1; echo "! $id $verb FAILED — continuing (re-run later; each component is independent)"; fi
      # Structured per-component verdict for machine callers (the adopt worker, ADR-0043); opt-in so
      # a human's terminal run isn't littered with it.
      [ "${AGENTOS_DRIVER_RESULT:-0}" = 1 ] && echo "AGENTOS-RESULT $id $([ "$crc" -eq 0 ] && echo ok || echo fail)"
      echo ;;
    sudo)   PRINTS+=("sudo $HERE/$cmd   # $id ($verb, needs root)") ;;
    manual) PRINTS+=("$HERE/$cmd   # $id ($verb)") ;;
  esac
done

if [ "${#PRINTS[@]}" -gt 0 ]; then
  echo "──────────────────────────────────────────────────────────────────────"
  echo "Privileged / manual steps — run these yourself (the driver never escalates):"
  for p in "${PRINTS[@]}"; do echo "    $p"; done
fi
