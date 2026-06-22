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
  --list         show the registry (+ which are default-on) and exit
  --all          select every component
  --defaults     select the default-on set (non-interactive)
  --only a,b     select exactly these ids
  --without a,b  the default-on set minus these ids
  --yes,-y       non-interactive; with no selector, implies --defaults
  (no args on a terminal) → interactive checklist
EOF
}

list_table() {
  printf '%-18s %-11s %-8s %-7s %s\n' ID TIER DEFAULT ROOT DESCRIPTION
  local i
  for ((i=0;i<N;i++)); do
    printf '%-18s %-11s %-8s %-7s %s\n' "${C_ID[$i]}" "${C_TIER[$i]}" "${C_DEF[$i]}" "${C_ROOT[$i]}" "${C_DESC[$i]}"
  done
}

declare -A SEL=()   # =() so ${#SEL[@]} is safe under set -u even when nothing is selected yet
sel_defaults() { local i; for ((i=0;i<N;i++)); do [ "${C_DEF[$i]}" = on ] && SEL["${C_ID[$i]}"]=1; done; }
sel_all()      { local i; for ((i=0;i<N;i++)); do SEL["${C_ID[$i]}"]=1; done; }
sel_csv()      { local id; IFS=',' read -ra a <<<"$1"; for id in "${a[@]}"; do id="$(trim "$id")"; [ -z "$id" ] && continue; idx_of "$id" >/dev/null || { echo "✗ unknown component: $id (see --list)" >&2; exit 2; }; SEL["$id"]=1; done; }
desel_csv()    { local id; IFS=',' read -ra a <<<"$1"; for id in "${a[@]}"; do id="$(trim "$id")"; [ -z "$id" ] && continue; idx_of "$id" >/dev/null || { echo "✗ unknown component: $id (see --list)" >&2; exit 2; }; unset "SEL[$id]" 2>/dev/null || true; done; }

interactive() {
  if command -v whiptail >/dev/null 2>&1; then
    local args=() i st chosen id
    for ((i=0;i<N;i++)); do st=OFF; [ "${C_DEF[$i]}" = on ] && st=ON; args+=("${C_ID[$i]}" "${C_DESC[$i]:0:46}" "$st"); done
    chosen="$(whiptail --title "AgentOS components — $MODE" --checklist \
      "Space toggles • Enter confirms • default-on preselected" 20 86 13 "${args[@]}" 3>&1 1>&2 2>&3)" \
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

SELECTOR="" ONLY="" WITHOUT="" YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --list) list_table; exit 0 ;;
    --all) SELECTOR=all ;;
    --defaults) SELECTOR=defaults ;;
    --only) shift; ONLY="${1:-}"; SELECTOR=only ;;
    --without) shift; WITHOUT="${1:-}"; SELECTOR=without ;;
    --yes|-y) YES=1 ;;
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
  "")       if [ "$YES" = 1 ] || [ ! -t 0 ]; then sel_defaults; else interactive; fi ;;
esac
[ "${#SEL[@]}" -gt 0 ] || { echo "nothing selected — see --list."; exit 0; }

verb=apply; [ "$MODE" = uninstall ] && verb=restore
echo "→ ${MODE}: ${!SEL[*]}"
echo
PRINTS=()   # =() (not `declare -a`) so an empty array is "set" for set -u when nothing is printed
seq_order=$(seq 0 $((N-1))); [ "$MODE" = uninstall ] && seq_order=$(seq $((N-1)) -1 0)
for i in $seq_order; do
  id="${C_ID[$i]}"; [ -n "${SEL[$id]:-}" ] || continue
  cmd="${C_APPLY[$i]}"; [ "$MODE" = uninstall ] && cmd="${C_RESTORE[$i]}"
  case "${C_ROOT[$i]}" in
    no)
      [ -n "$cmd" ] || { echo "! $id: empty $verb command in components.conf — skipping"; echo; continue; }
      echo "━━ $id ━━"
      ( cd "$HERE" && eval "./$cmd" ) || echo "! $id $verb FAILED — continuing (re-run later; each component is independent)"
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
