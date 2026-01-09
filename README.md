# Interaktywny Generator witrazy AI

Projekt obsługujący instalację artystyczną. Umożliwia generowanie obrazów witraży na podstawie odręcznych rysunków i szkiców.

## 🏗 Architektura Systemu

Projekt obsługujący instalację artystyczną. System podzielony jest na 3 części:

1. Aplikacja obsługująca skaner
2. Automatyzacja w GCS obsługująca generowanie witraży
3. Aplikacja lokalna obslugująca projektor

### 1. Warstwa Kliencka 

Działa fizycznie na sprzęcie dostępnym na wystawie. Składa się z mikroserwisów.

#### 1.1 login.py
Aplikacja okienkowa będąca UI:
 - Obsługuje zalogowanie użytkownika za pomocą kodu QR
 - Skanuje rysunki i przekazuje je do dalszej obróbki
 - weryfikuje ilość wykorzystanych generacji
 - obsługuje flagi w firestore

 #### 1.2 rotation.py

Aplikacja normalizująca zdjęcia przed dalszą obróbką:
- Na podstawie kodów QR obraca i przycina zdjęcia

#### 1.3 wrapper.py

Aplikacja łącząca ze sobą domyślną maskę (zdjęcie okna) ze szkicem wykonanym przez użytkownika:
- Wykorzystuje przygotowany plik full_map_config.json do wycięcią odpowiednich okien z rysunku użytkownika i nakłada je na obrazek bazowy window_mapper_window.jpg

#### 1.4 uploader.py

Aplikacja łącząca się z GCS i wysyłająca pliki

### 2. Warstwa Google Cloud Storage

#### 2.1 ai-core-witraz

Alikacja odpowiedzialna za obróbkę zdjęć przez AI
- Wykorzystany model generatywny: gemini-3-pro-image-preview
- Program działa w buckecie: stained-glass-bucket, pobiera pliki z folderu input, wygenerowane zdjęcia odkłada do output

### 3. Warstwa obsługi instalacji

Warstwa ta działa lokalnie na komputerze obsługującym projektor.

#### 3.1 downloader
Aplikacja pobierające wygenerowane obrazy z chmury na lokalne urządzenie
- Wykrywanie nowych plików odbywa się poprzez zmianę flag w firestore

#### 3.2 display_system

Aplikacja Tworząca karuzelę zdjęć umożliwiająca tworzenie kolejki witraży do wyświetlenia z możliwością dodawania witraży klienckich
- Wykorzystanie OSC umożliwia pracę z touchdesignerem
