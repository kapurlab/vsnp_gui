import { useMemo } from "react";

// FMDV reference scope picker: FMDV > serotype > lineage > genome, with
// "Auto" at every level. Picking a node above a genome means "closest
// available lineage within this node, chosen PER SAMPLE", which is what
// makes a serotype scope safe where a single fixed genome is not (within
// SAT1 the lineage genomes differ by up to 21%). See docs/FMDV_MODE.md.
//
// Emits the scope string the backend understands:
//   "FMDV" | "<SEROTYPE>" | "<SEROTYPE>/<lineage>" | "genome:<safe>"
export function parseScope(scope) {
  const s = (scope || "FMDV").trim();
  if (s.toUpperCase() === "FMDV") return { serotype: "", lineage: "", genome: "" };
  if (s.startsWith("genome:")) return { serotype: "", lineage: "", genome: s.slice(7) };
  const [sero, lin = ""] = s.split("/", 2);
  return { serotype: sero.toUpperCase(), lineage: lin, genome: "" };
}

export function buildScope({ serotype, lineage, genome }) {
  if (genome) return `genome:${genome}`;
  if (serotype && lineage) return `${serotype}/${lineage}`;
  if (serotype) return serotype;
  return "FMDV";
}

export default function FmdvScopePicker({ lineages, scope, onChange, disabled }) {
  const tree = lineages?.tree || {};
  const genomes = lineages?.genomes || [];
  const sel = parseScope(scope);

  // When a genome is pinned, show its serotype and lineage in the upper boxes
  // so the choice reads top-down.
  const pinned = sel.genome ? genomes.find((g) => g.safe === sel.genome) : null;
  const serotype = pinned ? pinned.serotype : sel.serotype;
  const lineage = pinned ? (pinned.lineage || "(unassigned)") : sel.lineage;

  const serotypes = useMemo(() => Object.keys(tree).sort(), [tree]);
  const lineagesFor = useMemo(
    () => (serotype && tree[serotype] ? Object.keys(tree[serotype]).sort() : []),
    [tree, serotype]);
  const genomesFor = useMemo(() => {
    if (!serotype) return [];
    return genomes.filter((g) => g.serotype === serotype
      && (!lineage || (g.lineage || "(unassigned)") === lineage));
  }, [genomes, serotype, lineage]);

  const set = (next) => onChange(buildScope(next));
  const lineageToken = (lin) => (lin === "(unassigned)" ? "" : lin);

  if (!lineages) {
    return <div className="muted" style={{ fontSize: "0.9em" }}>FMDV reference library not found. Build it once: <code>bin/bdtools setup-databases fmdv</code> on a bdtools install, or <code>bin/fmdv_library_build.sh</code> in this checkout.</div>;
  }

  return (
    <div className="fmdv-scope" style={{ display: "grid", gap: "0.4em" }}>
      <div className="muted" style={{ fontSize: "0.85em" }}>
        Reference is chosen per sample, as the closest lineage genome within this scope.
        {" "}{lineages.n_genomes} genomes in the library.
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: "0.5em" }}>
        <label>
          <span className="muted" style={{ fontSize: "0.8em" }}>Serotype</span>
          <select value={serotype} disabled={disabled}
            onChange={(e) => set({ serotype: e.target.value, lineage: "", genome: "" })}>
            <option value="">Auto (any serotype)</option>
            {serotypes.map((s) => <option key={s} value={s}>{s} ({Object.values(tree[s]).flat().length})</option>)}
          </select>
        </label>
        <label>
          <span className="muted" style={{ fontSize: "0.8em" }}>Topotype / lineage</span>
          <select value={lineage} disabled={disabled || !serotype}
            onChange={(e) => set({ serotype, lineage: lineageToken(e.target.value), genome: "" })}>
            <option value="">Auto (closest within {serotype || "serotype"})</option>
            {lineagesFor.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        </label>
        <label>
          <span className="muted" style={{ fontSize: "0.8em" }}>Genome</span>
          <select value={sel.genome} disabled={disabled || !serotype}
            onChange={(e) => set(e.target.value
              ? { serotype, lineage, genome: e.target.value }
              : { serotype, lineage: lineageToken(lineage), genome: "" })}>
            <option value="">Auto (closest)</option>
            {genomesFor.map((g) => (
              <option key={g.safe} value={g.safe}>{g.isolate} {g.year} ({g.accession})</option>
            ))}
          </select>
        </label>
      </div>
      <div style={{ fontSize: "0.85em" }}>
        Scope: <code>{buildScope({ serotype: sel.genome ? "" : serotype, lineage: sel.genome ? "" : lineageToken(lineage), genome: sel.genome })}</code>
        {sel.genome ? <span className="muted"> (pinned: every sample maps to this genome)</span> : null}
      </div>
    </div>
  );
}
