// Custos : à l'enregistrement d'un fichier, signale ses erreurs à la mascotte.
//
// Tout reste local : le message part sur le socket Unix de Custos ($XDG_RUNTIME_DIR/custos.sock).
// C'est Custos qui décide de s'inquiéter (seulement si le nombre d'erreurs augmente) ;
// rien n'est envoyé à Claude tant que tu ne cliques pas sur la mascotte.

const vscode = require("vscode");
const net = require("net");
const path = require("path");

const SETTLE = 800; // ms sans changement des erreurs avant de les signaler
const WATCH_AFTER_SAVE = 10000; // ms : le serveur de langage peut mettre un moment à répondre
const MAX_ERRORS = 15;

function socketPath() {
  const runtime = process.env.XDG_RUNTIME_DIR || `/run/user/${process.getuid()}`;
  return path.join(runtime, "custos.sock");
}

function send(message) {
  const socket = net.createConnection(socketPath());
  socket.on("error", () => {}); // Custos ne tourne pas : tant pis, en silence
  socket.end(JSON.stringify(message) + "\n");
}

function report(uri) {
  const errors = vscode.languages
    .getDiagnostics(uri)
    .filter((d) => d.severity === vscode.DiagnosticSeverity.Error);
  const folder = vscode.workspace.getWorkspaceFolder(uri);
  send({
    type: "code-problem",
    file: uri.fsPath,
    workspace: folder ? folder.uri.fsPath : "",
    count: errors.length,
    errors: errors.slice(0, MAX_ERRORS).map((d) => ({
      line: d.range.start.line + 1,
      message: String(d.message).split("\n")[0].slice(0, 300),
      source: d.source || "",
    })),
  });
}

function activate(context) {
  const saved = new Map(); // fichier → heure de son dernier enregistrement
  const timers = new Map();

  function later(uri) {
    const key = uri.toString();
    clearTimeout(timers.get(key));
    timers.set(key, setTimeout(() => {
      timers.delete(key);
      report(uri);
    }, SETTLE));
  }

  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument((document) => {
      if (document.uri.scheme !== "file") return;
      saved.set(document.uri.toString(), Date.now());
      later(document.uri);
    }),
    // Les erreurs arrivent souvent après l'enregistrement : on les suit un moment.
    vscode.languages.onDidChangeDiagnostics((event) => {
      for (const uri of event.uris) {
        const at = saved.get(uri.toString());
        if (at && Date.now() - at < WATCH_AFTER_SAVE) later(uri);
      }
    }),
    { dispose: () => timers.forEach((timer) => clearTimeout(timer)) },
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
