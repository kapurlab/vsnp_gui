import { useEffect, useState } from "react";

// The Inputs panel's shape: one row of source tabs, one source visible at a
// time, a sentence of context with the long explanation behind a "?", and
// the same status strip under every source. Four ways in used to stand in
// one column, each with its paragraph beside its controls, and nothing said
// which to reach for first. See docs/FMDV_MODE.md, "The Inputs panel".
//
// `fmdvOnly` is false for every tab since v0.4.155: a sequence can be brought
// into any project, where reads are simulated from it so that it runs through
// Step 1 like a sample (app.seq_import). The flag stays: the shape is useful.

export const INPUT_TABS = [
  { id: "minknow", label: "MinKNOW run", icon: "▤", fmdvOnly: false,
    line: "A run folder from the sequencer: one FASTQ per barcode, named from the sample sheet." },
  { id: "fastq", label: "FASTQ files", icon: "▦", fmdvOnly: false,
    line: "Read files you already have, on this computer or on the server." },
  { id: "sequences", label: "Sequences", icon: "≋", fmdvOnly: false,
    line: "A FASTA or GenBank record, or an accession: reads are made from it so it runs like a sample." },
  { id: "sra", label: "SRA", icon: "☁", fmdvOnly: false,
    line: "Public reads by run, experiment or sample accession." },
];

const STORAGE_KEY = "vsnp_gui.inputs.tab";

export function useInputTab(fmdvOn) {
  // The last tab used wins; before that, the sequencer's run folder in FMDV
  // mode (the protocol AHI runs) and plain FASTQ files otherwise.
  const [tab, setTab] = useState(() => {
    try { return window.localStorage.getItem(STORAGE_KEY) || ""; } catch { return ""; }
  });
  const fallback = fmdvOn ? "minknow" : "fastq";
  const allowed = INPUT_TABS.filter((t) => !t.fmdvOnly || fmdvOn).map((t) => t.id);
  const current = allowed.includes(tab) ? tab : fallback;
  useEffect(() => {
    if (tab && !allowed.includes(tab)) setTab(fallback);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fmdvOn]);
  const choose = (id) => {
    setTab(id);
    try { window.localStorage.setItem(STORAGE_KEY, id); } catch { /* per-browser nicety only */ }
  };
  return [current, choose];
}

export function InputTabs({ value, onChange, fmdvOn, disabled }) {
  const tabs = INPUT_TABS.filter((t) => !t.fmdvOnly || fmdvOn);
  return (
    <div className="input-tabs" role="tablist" aria-label="Where the inputs come from">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={value === t.id}
          className={`input-tab-btn${value === t.id ? " active" : ""}`}
          onClick={() => onChange(t.id)}
          disabled={disabled}
          title={t.line}
        >
          <span className="input-tab-icon" aria-hidden="true">{t.icon}</span>
          {t.label}
        </button>
      ))}
    </div>
  );
}

// One sentence, and the rest behind a "?" so the controls stand on their own.
export function InputHelp({ line, children }) {
  return (
    <div className="input-help">
      <span className="input-help-line">{line}</span>
      {children ? (
        <details className="input-help-more">
          <summary aria-label="More about this input" title="More about this input">?</summary>
          <div className="input-help-body">{children}</div>
        </details>
      ) : null}
    </div>
  );
}
