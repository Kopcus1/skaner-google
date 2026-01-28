import { app, BrowserWindow } from 'electron';
import { spawn } from 'child_process';
import path from 'path';

let pyProc = null;

const createPyProc = () => {
  // Uruchomienie skryptu Pythona w tle
  let script = path.join(__dirname, '../backend.py'); // Ścieżka do Twojego skryptu
  pyProc = spawn('python', [script]);

  pyProc.stdout.on('data', (data) => {
    console.log("PY:", data.toString());
  });
  pyProc.stderr.on('data', (data) => {
    console.log("PY ERR:", data.toString());
  });
}

const exitPyProc = () => {
  if (pyProc != null) pyProc.kill();
  pyProc = null;
}

app.on('ready', () => {
  createPyProc();
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    // kiosk: true, // Odkomentuj dla pełnego ekranu bez wyjścia
    webPreferences: {
      nodeIntegration: true,
    }
  });

  // Czekamy chwilę aż Flask wstanie, potem ładujemy UI
  setTimeout(() => {
    win.loadFile('index.html');
  }, 2000);
});

app.on('will-quit', exitPyProc);