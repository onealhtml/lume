<p align="center">
  <img src="assets/lume.png" width="128" alt="">
</p>

<h1 align="center">Lume</h1>

<p align="center">
  Painel rápido na bandeja para controlar uma lâmpada RGB Tuya<br>
  (Positivo Casa Inteligente, Smart Life etc.) direto pela rede local, sem nuvem.<br>
  Windows, Linux e macOS.
</p>

- Clique no ícone da bandeja (ou da barra de menus, no macOS): abre o painel com brilho, temperatura do branco, cores e roda de cores
- Rodinha do mouse e setas do teclado funcionam nos controles
- Reconecta sozinho se a lâmpada for desligada no interruptor ou mudar de IP
- Não ocupa a barra de tarefas nem o Dock

## Instalar e rodar

Precisa de Python 3.10 ou mais novo.

**Windows**

```
py -m pip install -r requirements.txt
pyw lume.pyw
```

**Linux / macOS**

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python lume.pyw
```

Na primeira vez abre a tela de configurações. Use **Login Smart Life**: no app Smart Life, vá em
Eu › engrenagem › Conta e segurança › Código de usuário, cole o código, escaneie o QR e clique em
**Salvar e conectar**. A lâmpada precisa estar pareada no Smart Life (ou Tuya Smart).

A opção **Iniciar com o sistema** cria a entrada certa para cada sistema (registro no Windows,
`~/.config/autostart` no Linux, LaunchAgent no macOS).

### Observações por sistema

- **Windows 11**: o painel usa o vidro acrílico do próprio sistema.
- **Linux**: o GNOME só mostra ícones de bandeja com a extensão *AppIndicator*. Sem bandeja, o Lume
  abre como janela comum. No Wayland o sistema decide onde a janela aparece.
- **macOS**: o ícone fica na barra de menus; "Fechar o Lume" fica nas configurações.

## Arquivos

| Arquivo | O que é |
|---|---|
| `lume.pyw` | o app: bandeja, janela e a ponte com a interface |
| `ui/Main.qml` | a interface (Qt Quick) |
| `core.py` | configuração, iniciar com o sistema e a conexão com a lâmpada |
| `smartlife_qr.py` | login por QR code no Smart Life para pegar a chave local |
| `platform_tweaks.py` | ajustes de cada sistema (vidro do Windows, esconder do Dock no macOS) |
| `icons.py` | desenha o ícone da bandeja na cor da lâmpada; `python icons.py` recria `assets/` |

A configuração (incluindo a chave local da lâmpada) fica fora do repositório, em
`%APPDATA%\Lume` (Windows), `~/.config/lume` (Linux) ou `~/Library/Application Support/Lume` (macOS).
