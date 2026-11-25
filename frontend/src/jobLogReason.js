// Why a finished job stopped, read from its log: the last line that is not
// blank, not one of the job manager's own "# ..." stamps (start, finish,
// duration) and not its "$ command" echo. For a script that died, that is the
// message it printed on the way out. "" when the log holds no such line.
export function failureLine(log) {
  const lines = String(log || "").split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (!line || line.startsWith("# ") || line.startsWith("$ ")) continue;
    return line.length > 300 ? `${line.slice(0, 300)}…` : line;
  }
  return "";
}

// What a job that succeeded still wants its user to know: the last line it
// printed that begins "NOTE: ", without that prefix. "" when there is none.
export function noteLine(log) {
  const lines = String(log || "").split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (line.startsWith("NOTE: ")) return line.slice(6).trim();
  }
  return "";
}
