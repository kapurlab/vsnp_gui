#!/usr/bin/env bash
# roar_import.sh — import an already-run vSNP3 project from PSU Roar into the
# vSNP GUI, WITHOUT re-running alignment.
#
# Codifies the hand-run Anthracis pilot (2026-06-08) as one repeatable command.
# Pulls the "functional set" (BAM + bai + zc.vcf + annotated VCF + reference +
# stats/report) — everything IGV and Step 2 need — but NOT the bulky fastq or
# the redundant intermediates. Optionally pulls fastq for a chosen subset (for
# Kraken), assembles + registers the reference option, and makes the reads
# visible to the GUI.
#
#   Usage:
#     # 1) open the Roar ControlMaster ONCE (one MFA), then run this:
#     ssh -fN roar            # see deploy: ~/.ssh/config `Host roar`
#     ./roar_import.sh <roar_project_path> <reference_name> [options]
#
#   Example (the Anthracis pilot):
#     ./roar_import.sh \
#       /storage/group/vxk1/default/Projects/Anthracis/vsnp/Bacillus_anthracis_ames_ancestor \
#       Bacillus_anthracis_ames \
#       --name Anthracis_ames_ancestor --fastq-filter 'OUAT|SVVU|IAH'
#
#   Arguments:
#     <roar_project_path>  source dir on Roar containing step1/ + step2/
#     <reference_name>     name for the GUI reference option (e.g.
#                          Bacillus_anthracis_ames). If an option by this name
#                          already exists, the reference phase is skipped.
#
#   Options:
#     --name NAME          dest project dir name (default: basename of source)
#     --dest DIR           projects root to import into (default: $HOME/projects)
#     --ref-options-dir D  reference-options root (default: first line of the
#                          vsnp3 reference_options_paths.txt, else autodetect)
#     --fastq-filter RE    ALSO pull fastq for step1 sample dirs matching this
#                          egrep pattern (for Kraken). Omit = no fastq.
#     --roar-host HOST     ssh alias for Roar (default: roar)
#     --skip-reference     don't assemble/register the reference option
#     --dry-run            print what would happen; transfer nothing
#     -h | --help          this help
#
#   Phases (all idempotent; rsync uses --append-verify):
#     preflight  transfer  verify  reference  fastq  symlink
#
# Companion to install_ood.sh. See docs memory roar-import-layout for context.

set -euo pipefail

ROAR_HOST="roar"
DEST_ROOT="${HOME}/projects"
REF_OPTIONS_DIR=""
FASTQ_FILTER=""
PROJECT_NAME=""
SKIP_REFERENCE=0
DRY_RUN=0
POSITIONAL=()

usage() { sed -n '2,46p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)            PROJECT_NAME="$2"; shift 2 ;;
    --dest)            DEST_ROOT="$2"; shift 2 ;;
    --ref-options-dir) REF_OPTIONS_DIR="$2"; shift 2 ;;
    --fastq-filter)    FASTQ_FILTER="$2"; shift 2 ;;
    --roar-host)       ROAR_HOST="$2"; shift 2 ;;
    --skip-reference)  SKIP_REFERENCE=1; shift ;;
    --dry-run)         DRY_RUN=1; shift ;;
    -h|--help)         usage; exit 0 ;;
    -*)                echo "unknown option: $1" >&2; exit 2 ;;
    *)                 POSITIONAL+=("$1"); shift ;;
  esac
done

[[ ${#POSITIONAL[@]} -eq 2 ]] || { echo "error: need <roar_project_path> <reference_name> (see --help)" >&2; exit 2; }
SRC="${POSITIONAL[0]%/}"
REFERENCE_NAME="${POSITIONAL[1]}"
[[ -n "${PROJECT_NAME}" ]] || PROJECT_NAME="$(basename "${SRC}")"
DEST_PROJ="${DEST_ROOT%/}/${PROJECT_NAME}"

c_bold=$'\e[1m'; c_dim=$'\e[2m'; c_grn=$'\e[32m'; c_ylw=$'\e[33m'; c_red=$'\e[31m'; c_rst=$'\e[0m'
log()  { echo "${c_bold}==>${c_rst} $*"; }
info() { echo "    $*"; }
ok()   { echo "    ${c_grn}ok${c_rst}   $*"; }
warn() { echo "    ${c_ylw}warn${c_rst} $*" >&2; }
die()  { echo "${c_red}error:${c_rst} $*" >&2; exit 1; }
run()  { if [[ ${DRY_RUN} -eq 1 ]]; then echo "    ${c_dim}[dry-run] $*${c_rst}"; else "$@"; fi; }
# rsh: run a command on Roar via the (already-authenticated) master.
rsh()  { ssh -o BatchMode=yes "${ROAR_HOST}" "$@"; }

# Functional-set excludes: fastq (pulled separately/on-demand), the unmapped-
# reads dir, and the huge uncompressed .sam intermediates the BAM supersedes.
FUNCTIONAL_EXCLUDES=(--exclude='*.fastq.gz' --exclude='unmapped_reads/' --exclude='*.sam')

# ---------------------------------------------------------------------------
MASTER_OK=0
phase_preflight() {
  log "preflight"
  # Roar master must be up (one interactive MFA, kept alive by ControlPersist).
  if ssh -O check "${ROAR_HOST}" >/dev/null 2>&1 || rsh true >/dev/null 2>&1; then
    MASTER_OK=1
  fi
  if [[ ${MASTER_OK} -eq 0 ]]; then
    local msg="can't reach Roar via '${ROAR_HOST}'. Open the master first: ssh -fN ${ROAR_HOST} (does the MFA once)."
    [[ ${DRY_RUN} -eq 1 ]] && { warn "${msg}"; warn "dry-run: continuing to preview the plan (source/verify checks skipped)"; }
    [[ ${DRY_RUN} -eq 1 ]] || die "${msg}"
  else
    ok "Roar reachable via '${ROAR_HOST}' (no re-auth)"
    rsh "test -d '${SRC}/step1'" || die "source has no step1/: ${SRC}"
    ok "source ok: ${SRC}"
  fi
  [[ -d "${DEST_ROOT}" ]] || run mkdir -p "${DEST_ROOT}"
  [[ -w "${DEST_ROOT}" || ${DRY_RUN} -eq 1 ]] || die "dest root not writable: ${DEST_ROOT}"
  ok "dest: ${DEST_PROJ}"
  # Resolve the reference-options dir (where GUI reference options live).
  if [[ -z "${REF_OPTIONS_DIR}" ]]; then
    for deps in /home/*/miniforge3/envs/vsnp3/dependencies/reference_options_paths.txt \
                /srv/*/tools/vsnp3/dependencies/reference_options_paths.txt; do
      [[ -f "${deps}" ]] && { REF_OPTIONS_DIR="$(head -1 "${deps}" 2>/dev/null)"; break; }
    done
  fi
  if [[ -n "${REF_OPTIONS_DIR}" ]]; then ok "reference options: ${REF_OPTIONS_DIR}"
  else warn "reference-options dir not found; pass --ref-options-dir to enable the reference phase"; fi
}

phase_transfer() {
  log "transfer — functional set (no fastq / unmapped_reads / sam)"
  run mkdir -p "${DEST_PROJ}"
  # Single stream: parallel over one MFA'd tunnel gave no speedup (link-capped).
  run rsync -ah --append-verify --info=progress2 "${FUNCTIONAL_EXCLUDES[@]}" \
      -e ssh "${ROAR_HOST}:${SRC}/" "${DEST_PROJ}/"
  ok "functional set in ${DEST_PROJ}"
}

phase_verify() {
  log "verify — counts vs source"
  if [[ ${MASTER_OK} -eq 0 ]]; then warn "Roar master down — skipping source comparison"; return 0; fi
  local sb sz sd db dz dd
  sb=$(rsh "find '${SRC}' -name '*_nodup.bam' 2>/dev/null | wc -l")
  sz=$(rsh "find '${SRC}' -name '*_zc.vcf' 2>/dev/null | wc -l")
  sd=$(rsh "ls '${SRC}/step2/vcf_database' 2>/dev/null | wc -l")
  db=$(find "${DEST_PROJ}" -name '*_nodup.bam' 2>/dev/null | wc -l)
  dz=$(find "${DEST_PROJ}" -name '*_zc.vcf' 2>/dev/null | wc -l)
  dd=$(ls "${DEST_PROJ}/step2/vcf_database" 2>/dev/null | wc -l)
  printf '    %-14s %10s %10s\n' "" source dest
  printf '    %-14s %10s %10s  %s\n' "bam"          "$sb" "$db" "$([[ "$sb" == "$db" ]] && echo ok || echo MISMATCH)"
  printf '    %-14s %10s %10s  %s\n' "zc.vcf"       "$sz" "$dz" "$([[ "$sz" == "$dz" ]] && echo ok || echo MISMATCH)"
  printf '    %-14s %10s %10s  %s\n' "vcf_database" "$sd" "$dd" "$([[ "$sd" == "$dd" ]] && echo ok || echo MISMATCH)"
  [[ "$sb" == "$db" && "$sz" == "$dz" && "$sd" == "$dd" ]] && ok "integrity verified" \
    || warn "counts differ — re-run transfer (rsync --append-verify resumes)"
}

phase_reference() {
  log "reference — assemble + register '${REFERENCE_NAME}'"
  [[ ${SKIP_REFERENCE} -eq 1 ]] && { info "skipped (--skip-reference)"; return 0; }
  [[ -n "${REF_OPTIONS_DIR}" ]] || { warn "no reference-options dir; skipping"; return 0; }
  local rodir="${REF_OPTIONS_DIR%/}/${REFERENCE_NAME}"
  if [[ -d "${rodir}" ]]; then ok "reference option already exists: ${rodir}"; return 0; fi
  # Discover the reference fasta from a sample's alignment dir (the dir name is
  # alignment_<accset>; the fasta is <accset>.fasta).
  local aln fasta
  aln="$(find "${DEST_PROJ}/step1" -maxdepth 2 -type d -name 'alignment_*' 2>/dev/null | head -1)"
  [[ -n "${aln}" ]] || { warn "no alignment dir found; cannot assemble reference"; return 0; }
  fasta="$(find "${aln}" -maxdepth 1 -name '*.fasta' 2>/dev/null | head -1)"
  [[ -n "${fasta}" ]] || { warn "no reference fasta in ${aln}; cannot assemble reference"; return 0; }
  run mkdir -p "${rodir}"
  run cp -p "${fasta}" "${rodir}/"
  [[ -f "${fasta}.fai" ]] && run cp -p "${fasta}.fai" "${rodir}/"
  # define_filter (Step 2 grouping) from step2.
  local df
  df="$(find "${DEST_PROJ}/step2" -iname '*define_filter*.xlsx' 2>/dev/null | head -1)"
  if [[ -n "${df}" ]]; then run cp -p "${df}" "${rodir}/"; ok "define_filter: $(basename "${df}")"
  else warn "no *define_filter*.xlsx found in step2 — Step 2 grouping won't have defining SNPs"; fi
  # GenBank annotation: copy any NC_*.gbk present in a sample dir (+ concat to
  # match the fasta stem). Best-effort — IGV/Step 2 work without it (no gene track).
  local gbkdir stem
  stem="$(basename "${fasta}" .fasta)"
  gbkdir="$(find "${DEST_PROJ}/step1" -maxdepth 2 -name '*.gbk' -printf '%h\n' 2>/dev/null | head -1)"
  if [[ -n "${gbkdir}" ]] && [[ ${DRY_RUN} -eq 0 ]]; then
    cp -p "${gbkdir}"/*.gbk "${rodir}/" 2>/dev/null || true
    cat "${gbkdir}"/*.gbk > "${rodir}/${stem}.gbk" 2>/dev/null || true
    ok "annotation: copied $(ls "${gbkdir}"/*.gbk 2>/dev/null | wc -l) gbk + concatenated"
  else
    warn "no .gbk in the tree — annotation/gene-track absent (fetch later with vsnp3_download_fasta_gbk_gff_by_acc.py if needed)"
  fi
  # .genome (chrom sizes) + empty best_reference.txt
  if [[ -f "${rodir}/$(basename "${fasta}").fai" ]]; then
    run bash -c "awk '{print \$1\"\\t\"\$2}' '${rodir}/$(basename "${fasta}").fai' > '${rodir}/${stem}.genome'"
  fi
  run bash -c ": > '${rodir}/best_reference.txt'"
  # Ensure the parent is registered so the GUI lists it.
  local deps
  for deps in /home/*/miniforge3/envs/vsnp3/dependencies/reference_options_paths.txt \
              /srv/*/tools/vsnp3/dependencies/reference_options_paths.txt; do
    [[ -f "${deps}" ]] || continue
    if ! grep -qxF "${REF_OPTIONS_DIR%/}" "${deps}" 2>/dev/null; then
      run bash -c "echo '${REF_OPTIONS_DIR%/}' >> '${deps}'"; ok "registered ${REF_OPTIONS_DIR%/} in ${deps##*/}"
    fi
    break
  done
  ok "reference option assembled: ${rodir}"
  info "NOTE: this is a best-effort multi-record assembly; confirm Step 2 grouping in a GUI run."
}

phase_fastq() {
  [[ -n "${FASTQ_FILTER}" ]] || { log "fastq — skipped (no --fastq-filter)"; return 0; }
  log "fastq — pull reads for samples matching /${FASTQ_FILTER}/ (for Kraken)"
  [[ ${MASTER_OK} -eq 0 ]] && { warn "Roar master down — would pull fastq for /${FASTQ_FILTER}/ samples"; return 0; }
  local dirs
  dirs="$(rsh "ls -1 '${SRC}/step1'" | grep -iE "${FASTQ_FILTER}" | sed 's#^#step1/#' || true)"
  local n; n="$(printf '%s\n' "${dirs}" | grep -c . || true)"
  [[ "${n:-0}" -gt 0 ]] || { warn "no step1 sample dirs match /${FASTQ_FILTER}/"; return 0; }
  info "matched ${n} sample dirs"
  if [[ ${DRY_RUN} -eq 1 ]]; then echo "    ${c_dim}[dry-run] rsync real reads for ${n} dirs${c_rst}"; return 0; fi
  printf '%s\n' "${dirs}" > /tmp/roar_import_fqdirs.$$
  # real paired reads only — exclude the *_unmapped_* subset.
  rsync -ah -r --info=progress2 \
    --include='*/' --include='*_R[12].fastq.gz' --exclude='*_unmapped_*' --exclude='*' \
    --files-from=/tmp/roar_import_fqdirs.$$ -e ssh "${ROAR_HOST}:${SRC}/" "${DEST_PROJ}/"
  rm -f /tmp/roar_import_fqdirs.$$
  ok "fastq pulled for ${n} samples"
}

phase_symlink() {
  log "symlink — make reads visible in download/ (both GUIs count it)"
  run mkdir -p "${DEST_PROJ}/download"
  if [[ ${DRY_RUN} -eq 1 ]]; then echo "    ${c_dim}[dry-run] ln -sf step1 reads -> download/ (excl *_unmapped_*)${c_rst}"; return 0; fi
  ( cd "${DEST_PROJ}/download" && find ../step1 -name '*_R[12].fastq.gz' ! -name '*_unmapped_*' -type f \
      -exec ln -sf {} . \; ) 2>/dev/null || true
  local n; n="$(find -L "${DEST_PROJ}/download" -maxdepth 1 -name '*.fastq.gz' -type f 2>/dev/null | wc -l)"
  ok "${n} read files linked into download/"
}

# ---------------------------------------------------------------------------
echo "${c_bold}Roar import${c_rst}  ${SRC}  ->  ${DEST_PROJ}   (ref: ${REFERENCE_NAME})"
[[ ${DRY_RUN} -eq 1 ]] && echo "${c_ylw}DRY RUN — nothing transferred${c_rst}"
echo
phase_preflight; echo
phase_transfer;  echo
phase_verify;    echo
phase_reference; echo
phase_fastq;     echo
phase_symlink;   echo
log "import done: ${DEST_PROJ}"
info "Open it in the vSNP GUI (reference '${REFERENCE_NAME}'); run Step 2 / Kraken as needed."
