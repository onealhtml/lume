# Changelog

## [1.2.0]

- Several lamps: pick one at the top of the panel, or **All** to control every lamp at once.
  Each lamp keeps its own connection, so one switched off at the wall doesn't stop the others.
- Smart Life import: after signing in, choose which lamps from your account to add. Lume finds each
  one on your network by itself.
- Lume can stay signed in to Smart Life, so adding lamps later doesn't need the QR code again. The
  sign-in is encrypted by the system (DPAPI on Windows, Keychain on macOS, Secret Service on Linux);
  where there is no system keystore it isn't kept. **Sign out** forgets it.
- The tray menu lists every lamp; click one to turn it on or off.
- Settings lists the lamps to rename, remove or add by hand.
- Support for Tuya bulbs that use the older data layouts (the tinytuya "A" and "C" types), including
  white-only bulbs.
- Settings from earlier versions are converted automatically.

## [1.1.3]

- Linux: Lume adds itself to the app menu, with its icon, the first time it runs. GNOME now knows the
  panel belongs to Lume; before, it counted it as a window of whatever app opened Lume (Files, a
  terminal), and closing that app could crash GNOME Shell and log you out.
- macOS: sharper app icon (1024 px).

## [1.1.2]

- The Linux AppImage and tar.gz open again on newer distros (Fedora 44 and others): they no longer
  ship system libraries such as libstdc++ that broke the graphics driver, which made Lume close
  right at startup.

## [1.1.1]

- The brightness percentage turns dark as soon as the bright part of the bar reaches it (it stayed
  white, and unreadable, below 18%).

## [1.1.0]

- Follows the system language: English, Portuguese, Spanish, French, German, Italian, Japanese and
  Simplified Chinese. English is used when the system language isn't one of these.
- The Windows installer also follows the system language.

## [1.0.1]

- Colour changes are instant again: Lume now sends the lamp only what changed. Repeating "on" and
  "colour mode" with every change made the lamp replay its fade transition.
- The sun icon on the brightness bar turns dark over the bright part of the bar, so it no longer
  disappears at high brightness.

## [1.0.0]

First release.

- Tray panel (menu bar on macOS) with brightness, white temperature, colour presets and a colour wheel.
- Mouse wheel and keyboard arrows work on the sliders.
- Talks to the lamp over the local network; reconnects by itself when the lamp is switched off at the
  wall or gets a new IP.
- Smart Life QR login to get the lamp's local key.
- Windows 11 acrylic or light glass, chosen in Settings.
- Start with the system on Windows, Linux and macOS.
- Installers for Windows, Linux (AppImage and tar.gz) and macOS (Apple Silicon and Intel).
