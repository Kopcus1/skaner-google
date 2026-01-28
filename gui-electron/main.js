const { app, BrowserWindow } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

let pyProc = null;

const createPyProc = () => {
  // 1. Ścieżka do skryptu Python (jeden folder wyżej)
  // Upewnij się, że Twój plik z serwerem nazywa się login.py (widzę też login_server.py na screenie - wybierz właściwy!)
  let scriptPath = path.join(__dirname, '..', 'login.py');

  // 2. Ścieżka do Pythona w Twoim folderze .venv
  // To jest KLUCZOWE - używamy Pythona, który ma zainstalowane biblioteki
  let pythonPath = path.join(__dirname, '..', '.venv', 'Scripts', 'python.exe');

  console.log('--- STARTOWANIE PYTHONA ---');
  console.log('Python path: ' + pythonPath);
  console.log('Script path: ' + scriptPath);


  // 3. Uruchamiamy proces
  // DODANO '-u' -> To wymusza natychmiastowe wyświetlanie logów (Unbuffered)
  pyProc = spawn(pythonPath, ['-u', scriptPath], {
    cwd: path.join(__dirname, '..'),
    windowsHide: true 
  });

  // Logowanie błędów z Pythona do konsoli Electrona
  pyProc.stdout.on('data', (data) => console.log('PY LOG:', data.toString()));
  pyProc.stderr.on('data', (data) => console.log('PY ERR:', data.toString()));
};

const exitPyProc = () => {
  if (pyProc) pyProc.kill();
  pyProc = null;
};

function createWindow () {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    }
  });

  // Czekamy chwilę dłużej na start Pythona (3 sekundy)
  setTimeout(() => {
    win.loadFile('index.html');
  }, 3000);
  
  // Opcjonalnie: Otwórz narzędzia deweloperskie, żeby widzieć błędy
  // win.webContents.openDevTools();
}

app.whenReady().then(() => {
  createPyProc();
  createWindow();
});

app.on('will-quit', exitPyProc);
app.on('window-all-closed', () => {
  exitPyProc();
  if (process.platform !== 'darwin') app.quit();
});