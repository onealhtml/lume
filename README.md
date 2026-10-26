<img src="assets/lume.png" width="96" align="right">

# Lume

Painel rápido na bandeja do Windows 11 para controlar uma lâmpada RGB Tuya (Positivo Casa Inteligente, Smart Life, etc.) direto pela rede local, sem nuvem.

- Clique no ícone da bandeja: abre o painel (brilho, temperatura do branco, cores, roda de cores)
- Botão direito: ligar/desligar e sair
- Reconecta sozinho se a lâmpada for desligada no interruptor ou mudar de IP
- Não ocupa espaço na barra de tarefas: vive só na bandeja

## Rodar

```
py -3.15 -m pip install -r requirements.txt
pyw -3.15 lume.pyw
```

Na primeira vez abre a tela de configurações. Use **Login Smart Life**: no app Smart Life, vá em
Eu › engrenagem › Conta e segurança › Código de usuário, cole o código, escaneie o QR e clique em
**Salvar e conectar**. A lâmpada precisa estar pareada no Smart Life (ou Tuya Smart).

Os ícones são gerados por código: `py -3.15 icons.py` recria `assets/`.

A configuração (incluindo a chave local) fica em `config.json`, que não vai para o git.
