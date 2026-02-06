import shutil
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
    fasterq = shutil.which("fasterq-dump") or "fasterq-dump"

    acc_block = "\n".join([f'    "{a}"' for a in accessions])

    return f"""#!/bin/bash
set -euo pipefail

cd "{download_dir}"

echo "Using fasterq-dump: {fasterq}"

ACCESSIONS=(
{acc_block}
)

SRA_OK=0
echo "== Attempt SRA Toolkit first =="
if command -v prefetch >/dev/null 2>&1 && command -v fasterq-dump >/dev/null 2>&1; then
  for acc in "${{ACCESSIONS[@]}}"; do
    # Skip if we already have gzipped reads for this accession
    if ls "${{acc}}"*_1.fastq.gz "${{acc}}"*_R1*.fastq.gz 2>/dev/null | head -1 | grep -q .; then
      echo "Already have reads for $acc, skipping"
      continue
    fi
    echo "Prefetch $acc"
    if prefetch "$acc"; then
      echo "fasterq-dump --split-3 $acc"
      if fasterq-dump --split-3 "$acc"; then
        # fasterq-dump produces uncompressed .fastq — gzip them
        for fq in "${{acc}}"*.fastq; do
          if [ -f "$fq" ]; then
            echo "Compressing $fq"
            gzip "$fq"
          fi
        done
        SRA_OK=1
      fi
    fi
  done
else
  echo "SRA toolkit not available, skipping"
fi

echo "== ENA fallback for any missing accessions =="
for acc in "${{ACCESSIONS[@]}}"; do
  # Skip if we already have reads (from SRA toolkit or prior download)
  if ls "${{acc}}"*.fastq.gz 2>/dev/null | head -1 | grep -q .; then
    echo "Already have reads for $acc, skipping ENA"
    continue
  fi
  echo "Fetching ENA URLs for $acc"
  URLS=$(curl -s {curl_insecure} "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=$acc&result=read_run&fields=fastq_ftp" | tail -n1 | cut -f2)
  if [ -n "$URLS" ] && [ "$URLS" != "fastq_ftp" ]; then
    IFS=';' read -ra URL_ARRAY <<< "$URLS"
    for url in "${{URL_ARRAY[@]}}"; do
      filename=$(basename "$url")
      echo "Downloading $filename"
      if ! curl -f {curl_insecure} "ftp://$url" -o "$filename"; then
        echo "FTP failed, trying HTTPS"
        curl -f {curl_insecure} "https://$url" -o "$filename" || true
      fi
    done
  else
    echo "ENA did not return URLs for $acc"
  fi
done

# Compress any remaining uncompressed .fastq files
for fq in *.fastq; do
  if [ -f "$fq" ]; then
    echo "Compressing $fq"
    gzip "$fq"
  fi
done

echo "== Summary =="
if ls *.fastq.gz >/dev/null 2>&1; then
  echo "Downloaded FASTQ.GZ files:"; ls -lh *.fastq.gz
else
  echo "WARNING: No .fastq.gz files found after download"
fi
"""
