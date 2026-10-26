Painel rápido na bandeja para controlar uma lâmpada RGB Tuya (Positivo Casa Inteligente, Smart Life etc.) pela rede local.

## Qual arquivo baixar

| Sistema | Arquivo |
|---|---|
| **Windows 10/11** | `Lume-…-Windows-Setup.exe` |
| **Linux** | `Lume-…-Linux-x86_64.AppImage` (ou o `.tar.gz`) |
| **macOS Apple Silicon** (M1 em diante) | `Lume-…-macOS-arm64.dmg` |
| **macOS Intel** | `Lume-…-macOS-x86_64.dmg` |

## Primeira vez

**Windows:** o instalador não pede administrador. Como o app não tem assinatura digital paga, o
SmartScreen pode avisar: clique em *Mais informações* → *Executar assim mesmo*.

**Linux:** dê permissão de execução e abra:

```
chmod +x Lume-*.AppImage
./Lume-*.AppImage
```

Se reclamar de FUSE, instale `libfuse2` (Ubuntu: `sudo apt install libfuse2t64`) ou rode com
`--appimage-extract-and-run`. No GNOME o ícone da bandeja precisa da extensão *AppIndicator*;
sem ela o Lume abre como janela comum.

**macOS:** arraste o Lume para Aplicativos. Como o app não é assinado pela Apple, na primeira vez
clique com o botão direito no Lume → *Abrir* → *Abrir*. Se o macOS disser que o app está
"danificado", rode no Terminal:

```
xattr -dr com.apple.quarantine /Applications/Lume.app
```

Depois é só configurar: **Login Smart Life** nas configurações, escanear o QR e **Salvar e conectar**.
