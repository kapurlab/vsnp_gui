import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List


def expand_accessions(accessions: List[str]) -> List[str]:
    expanded: List[str] = []
    for acc in accessions:
        acc = acc.strip()
        if not acc:
            continue
        if acc.startswith(("SRR", "ERR", "DRR")):
            expanded.append(acc)
            continue
        expanded.extend(_expand_single(acc))
    return expanded


def _expand_single(accession: str) -> List[str]:
    try:
        url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            f"?db=sra&term={accession}&usehistory=y"
        )
        with urllib.request.urlopen(url, timeout=10) as response:
            xml_data = response.read()
        root = ET.fromstring(xml_data)
        ids = [e.text for e in root.findall(".//Id") if e.text]
        if not ids:
            return [accession]
        fetch_url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            f"?db=sra&id={','.join(ids)}"
        )
        with urllib.request.urlopen(fetch_url, timeout=30) as response:
            fetch_xml = response.read()
        fetch_root = ET.fromstring(fetch_xml)
        runs = []
        for run in fetch_root.findall(".//RUN"):
            run_acc = run.get("accession")
            if run_acc:
                runs.append(run_acc)
        return runs or [accession]
    except Exception:
        return [accession]


def build_download_script(download_dir: Path, accessions: List[str], allow_insecure_https: bool) -> str:
    curl_insecure = "-k" if allow_insecure_https else ""
    acc_block = "\n".join([f'    "{a}"' for a in accessions])

    return f"""#!/bin/bash
set -u

cd "{download_dir}"

# ── Tool detection ──────────────────────────────────────────────
HAS_WGET=0; HAS_FASTERQ=0; HAS_ENADATAGET=0; HAS_CURL=0; HAS_PIGZ=0

if command -v wget >/dev/null 2>&1; then
  HAS_WGET=1; echo "[OK] wget found: $(command -v wget)"
else
  echo "[MISSING] wget — Method 1 (S3 direct download) will be skipped"
fi

if command -v fasterq-dump >/dev/null 2>&1; then
  HAS_FASTERQ=1; echo "[OK] fasterq-dump found: $(command -v fasterq-dump)"
else
  echo "[MISSING] fasterq-dump — Method 1 (SRA conversion) will be skipped"
fi

if command -v enaDataGet >/dev/null 2>&1; then
  HAS_ENADATAGET=1; echo "[OK] enaDataGet found: $(command -v enaDataGet)"
else
  echo "[MISSING] enaDataGet — Method 2 (ENA Data Get) will be skipped"
fi

if command -v curl >/dev/null 2>&1; then
  HAS_CURL=1; echo "[OK] curl found: $(command -v curl)"
else
  echo "[MISSING] curl — Method 3 (ENA HTTPS/FTP) will be skipped"
fi

if command -v pigz >/dev/null 2>&1; then
  HAS_PIGZ=1; echo "[OK] pigz found (fast compression)"
else
  echo "[OK] pigz not found, will use gzip"
fi

# Check that at least one download method is available
CAN_METHOD1=0; CAN_METHOD2=0; CAN_METHOD3=0
if [ "$HAS_WGET" -eq 1 ] && [ "$HAS_FASTERQ" -eq 1 ]; then CAN_METHOD1=1; fi
if [ "$HAS_ENADATAGET" -eq 1 ]; then CAN_METHOD2=1; fi
if [ "$HAS_CURL" -eq 1 ]; then CAN_METHOD3=1; fi

if [ "$CAN_METHOD1" -eq 0 ] && [ "$CAN_METHOD2" -eq 0 ] && [ "$CAN_METHOD3" -eq 0 ]; then
  echo "[DEPENDENCY_ERROR] No download methods available. Install wget+fasterq-dump, enaDataGet, or curl."
  exit 1
fi

echo ""

# ── Helper functions ────────────────────────────────────────────

already_have() {{
  local acc="$1"
  # Check for paired-end files
  if ls "${{acc}}"_1.fastq.gz "${{acc}}"_2.fastq.gz 2>/dev/null | head -1 | grep -q .; then
    return 0
  fi
  # Check for single-end file
  if [ -f "${{acc}}.fastq.gz" ]; then
    return 0
  fi
  return 1
}}

compress_fastqs() {{
  local acc="$1"
  for fq in "${{acc}}"*.fastq; do
    if [ -f "$fq" ]; then
      echo "  Compressing $fq"
      if [ "$HAS_PIGZ" -eq 1 ]; then
        pigz "$fq"
      else
        gzip "$fq"
      fi
    fi
  done
}}

# ── Method 1: S3 wget + fasterq-dump ───────────────────────────

method1() {{
  local acc="$1"
  if [ "$CAN_METHOD1" -eq 0 ]; then return 1; fi

  echo "  [Method 1] S3 wget + fasterq-dump"
  local sra_file="${{acc}}.sra"

  # Download .sra from S3
  if ! wget -q -O "$sra_file" "https://sra-pub-run-odp.s3.amazonaws.com/sra/${{acc}}/${{acc}}" 2>&1; then
    echo "  [Method 1] wget download failed"
    rm -f "$sra_file"
    return 1
  fi

  if [ ! -s "$sra_file" ]; then
    echo "  [Method 1] Downloaded file is empty"
    rm -f "$sra_file"
    return 1
  fi

  # Convert with fasterq-dump --split-3 (handles single and paired)
  if ! fasterq-dump --split-3 "$sra_file" 2>&1; then
    echo "  [Method 1] fasterq-dump conversion failed"
    rm -f "$sra_file"
    return 1
  fi

  rm -f "$sra_file"
  compress_fastqs "$acc"
  return 0
}}

# ── Method 2: enaDataGet ───────────────────────────────────────

method2() {{
  local acc="$1"
  if [ "$CAN_METHOD2" -eq 0 ]; then return 1; fi

  echo "  [Method 2] enaDataGet"
  if ! enaDataGet --format fastq "$acc" 2>&1; then
    echo "  [Method 2] enaDataGet failed"
    return 1
  fi

  # enaDataGet puts files in a subdirectory named after the accession
  if [ -d "$acc" ]; then
    mv "$acc"/*.fastq.gz . 2>/dev/null || true
    mv "$acc"/*.fastq . 2>/dev/null || true
    rmdir "$acc" 2>/dev/null || true
  fi

  compress_fastqs "$acc"
  return 0
}}

# ── Method 3: ENA curl (HTTPS then FTP fallback) ───────────────

method3() {{
  local acc="$1"
  if [ "$CAN_METHOD3" -eq 0 ]; then return 1; fi

  echo "  [Method 3] ENA curl"
  local api_url="https://www.ebi.ac.uk/ena/portal/api/filereport?accession=${{acc}}&result=read_run&fields=fastq_ftp"
  local urls
  urls=$(curl -s {curl_insecure} "$api_url" | tail -n1 | cut -f2)

  if [ -z "$urls" ] || [ "$urls" = "fastq_ftp" ]; then
    echo "  [Method 3] ENA did not return URLs for $acc"
    return 1
  fi

  local all_ok=1
  IFS=';' read -ra url_array <<< "$urls"
  for url in "${{url_array[@]}}"; do
    local filename
    filename=$(basename "$url")
    echo "  Downloading $filename"
    # Try HTTPS first, then FTP
    if curl -f {curl_insecure} "https://$url" -o "$filename" 2>&1; then
      echo "  [OK] $filename downloaded via HTTPS"
    elif curl -f {curl_insecure} "ftp://$url" -o "$filename" 2>&1; then
      echo "  [OK] $filename downloaded via FTP"
    else
      echo "  [FAILED] Could not download $filename"
      rm -f "$filename"
      all_ok=0
    fi
  done

  if [ "$all_ok" -eq 0 ]; then return 1; fi
  return 0
}}

# ── Main download loop ─────────────────────────────────────────

ACCESSIONS=(
{acc_block}
)

SUCCEEDED=0
FAILED_COUNT=0
FAILED_LIST=""

for acc in "${{ACCESSIONS[@]}}"; do
  echo "── $acc ──"

  if already_have "$acc"; then
    echo "  Already have reads for $acc, skipping"
    SUCCEEDED=$((SUCCEEDED + 1))
    continue
  fi

  downloaded=0
  for method in method1 method2 method3; do
    if $method "$acc"; then
      downloaded=1
      break
    fi
  done

  if [ "$downloaded" -eq 1 ] && already_have "$acc"; then
    echo "  [OK] $acc downloaded successfully"
    SUCCEEDED=$((SUCCEEDED + 1))
  else
    echo "  [FAILED] All methods failed for $acc"
    FAILED_COUNT=$((FAILED_COUNT + 1))
    FAILED_LIST="$FAILED_LIST $acc"
  fi
  echo ""
done

# ── Summary ─────────────────────────────────────────────────────

echo "== Summary =="
echo "Succeeded: $SUCCEEDED / ${{#ACCESSIONS[@]}}"

if [ "$FAILED_COUNT" -gt 0 ]; then
  echo "[FAILED] Failed accessions:$FAILED_LIST"
fi

if ls *.fastq.gz >/dev/null 2>&1; then
  echo ""; echo "Downloaded files:"
  ls -lh *.fastq.gz
else
  echo "[FAILED] No .fastq.gz files found after download"
fi

if [ "$FAILED_COUNT" -gt 0 ] && [ "$SUCCEEDED" -eq 0 ]; then
  exit 1
fi
exit 0
"""
