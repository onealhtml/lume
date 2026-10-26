<p align="center">
  <img src="assets/lume.png" width="128" alt="">
</p>

<h1 align="center">Lume</h1>

<p align="center">
  Painel rápido na bandeja para controlar uma lâmpada RGB Tuya (Smart Life, Positivo Casa Inteligente<br>
  e outras marcas baseadas em Tuya) direto pela rede local, sem nuvem.<br>
  Windows, Linux e macOS.
</p>

<p align="center"><a href="README.md">Read in English</a></p>

- Clique no ícone da bandeja (na barra de menus, no macOS) para abrir o painel: brilho, temperatura do
  branco, cores prontas e roda de cores
- Rodinha do mouse e setas do teclado funcionam nos controles
- Reconecta sozinho se a lâmpada for desligada no interruptor ou mudar de IP
- Não ocupa a barra de tarefas nem o Dock
- Segue o idioma do sistema (inglês, português, espanhol, francês, alemão, italiano, japonês e
  chinês simplificado)

## Baixar

A versão mais nova está em [Releases](https://github.com/onealhtml/lume/releases/latest):

| Sistema | Arquivo |
|---|---|
| Windows 10/11 | `Lume-…-Windows-Setup.exe` |
| Linux | `Lume-…-Linux-x86_64.AppImage` (ou o `.tar.gz`) |
| macOS Apple Silicon (M1 em diante) | `Lume-…-macOS-arm64.dmg` |
| macOS Intel | `Lume-…-macOS-x86_64.dmg` |

O que mudou em cada versão está no [changelog](CHANGELOG.md) (em inglês).

## Instalar

**Windows:** rode o instalador; ele não pede administrador. O Lume não tem assinatura digital paga,
então o SmartScreen pode avisar: clique em *Mais informações* → *Executar assim mesmo*.

**Linux:** dê permissão de execução ao AppImage e abra:

```
chmod +x Lume-*.AppImage
./Lume-*.AppImage
```

Se reclamar de FUSE, instale `libfuse2` (Ubuntu: `sudo apt install libfuse2t64`) ou rode com
`--appimage-extract-and-run`.

**macOS:** arraste o Lume para Aplicativos. Ele não é autenticado pela Apple, então na primeira vez
clique com o botão direito → *Abrir* → *Abrir*. Se o macOS disser que o app está "danificado", rode:

```
xattr -dr com.apple.quarantine /Applications/Lume.app
```

## Configurar

Na primeira vez o Lume abre as configurações. Clique em **Login Smart Life**; no app Smart Life vá em
Eu › engrenagem › Conta e segurança › Código de usuário, cole o código, escaneie o QR e clique em
**Salvar e conectar**. A lâmpada precisa estar pareada no Smart Life (ou Tuya Smart).

**Iniciar com o sistema** cria a entrada certa em cada sistema (registro no Windows,
`~/.config/autostart` no Linux, LaunchAgent no macOS).

### Observações por sistema

- **Windows 11:** o painel usa o vidro acrílico do sistema, ou um desfoque mais leve (Configurações › Vidro).
- **Linux:** o GNOME só mostra ícones de bandeja com a extensão *AppIndicator* (o Ubuntu já vem com ela;
  o KDE e a maioria dos outros ambientes têm bandeja própria). Sem bandeja, o Lume abre como janela
  comum. No Wayland o sistema decide onde a janela aparece. Para ter o ícone na barra de cima do GNOME:

  ```
  sudo dnf install gnome-shell-extension-appindicator      # Fedora
  sudo apt install gnome-shell-extension-appindicator      # Debian
  sudo pacman -S gnome-shell-extension-appindicator        # Arch
  ```

  Saia e entre de novo na sessão, rode `gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com`
  e abra o Lume outra vez.
- **macOS:** o ícone fica na barra de menus; "Fechar o Lume" fica nas configurações.

## Rodar pelo código

Detalhes de desenvolvimento e o que é cada arquivo estão no [README em inglês](README.md#run-from-source).
