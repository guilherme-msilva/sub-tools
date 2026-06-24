# ⬡ Sub-Tools

> **Automatic subtitle downloader, cleaner and synchronizer**
> Baixador e sincronizador automático de legendas

---

## 🇺🇸 English

### What is Sub-Tools?

Sub-Tools is a lightweight Python desktop utility with a modern dark-themed GUI that automates three common subtitle-related tasks:

| Feature | What it does |
|---|---|
| **⬇ Download Subtitles** | Searches OpenSubtitles.com and downloads `.srt` files for every video in a folder |
| **✦ Clean Subtitles** | Removes HTML formatting tags and advertisement blocks from `.srt` files |
| **🔄 Sync Subtitles** | Re-synchronizes a subtitle file against its video using the [Alass](https://github.com/kaegi/alass) tool |

---

### Screenshots / Interface

The application window is divided into two tabs:

- **⬇ Download & Clean** — Select a folder, choose a language, and let Sub-Tools find and download all missing subtitles. Optional: auto-clean and auto-sync right after each download.
- **🔄 Subtitle Syncing** — Pick a single video and a misaligned subtitle, and Alass will re-time it perfectly.

---

### Requirements

| Dependency | Purpose | Install |
|---|---|---|
| Python 3.9+ | Runtime | [python.org](https://python.org) |
| `opensubtitlescom` | OpenSubtitles REST API client | `pip install opensubtitlescom` |
| `python-dotenv` | Load credentials from `.env` | `pip install python-dotenv` |
| Alass _(optional)_ | Subtitle synchronization engine | See [Alass setup](#alass-setup) below |

Install Python dependencies in one command:

```bash
pip install opensubtitlescom python-dotenv
```

---

### Installation

1. **Clone or download** this repository:
   ```bash
   git clone https://github.com/your-user/sub-tools.git
   cd sub-tools
   ```

2. **Install dependencies:**
   ```bash
   pip install opensubtitlescom python-dotenv
   ```

3. **Configure credentials** — create a `.env` file in the project folder (or use the Settings dialog inside the app):
   ```env
   MY_API_KEY=your_opensubtitles_api_key
   MY_USERNAME=your_username        # optional — raises daily download limit
   MY_PASSWORD=your_password        # optional — required if username is set
   ```
   > Get a free API key at [opensubtitles.com/en/api](https://www.opensubtitles.com/en/api)

4. **Run the application:**
   ```bash
   python legendaz.py
   ```

---

### Alass Setup

[Alass](https://github.com/kaegi/alass) is a command-line tool that automatically re-synchronizes subtitles by analyzing the audio track of the video.

1. Download the latest Windows release from the [Alass releases page](https://github.com/kaegi/alass/releases).
2. Extract it and place the files inside a folder named `alass-windows64` **in the same directory as `legendaz.py`**:

```
sub-tools/
├── legendaz.py
├── .env
└── alass-windows64/
    ├── alass.bat          ← launcher (recommended)
    └── alass.exe          ← executable
```

Sub-Tools will detect Alass automatically on startup and show a green status indicator in the Subtitle Syncing tab.

---

### Feature Details

#### ⬇ Download Subtitles

- Scans a folder **recursively** for video files (`.mp4`, `.mkv`, `.avi`, `.mov`, `.wmv`, `.flv`, `.m4v`, `.ts`)
- Searches OpenSubtitles.com by filename
- Downloads the best match in the selected language (with fallback codes)
- **15 languages** supported, with preference saved automatically to `.env`
- **Skip existing** option: skip videos that already have a subtitle file
- When "skip" is off, downloads the new subtitle with a language code suffix (e.g. `movie.en.srt`) to avoid overwriting

#### ✦ Clean Subtitles

- Scans all `.srt` files in the selected folder recursively
- **Removes formatting tags**: `<font>`, `<b>`, `<i>`, `<u>` and any other HTML tags
- **Filters advertisement blocks**: entire subtitle blocks whose text contains any keyword from a configurable list are flagged for removal
- **Confirmation dialog**: before removing any block, a modal shows each flagged block with its text, timestamp and matched keyword — the user checks/unchecks which ones to actually delete (false-positive protection)
- **Renumbers** remaining blocks sequentially so the `.srt` file stays valid
- **Writes a `.log` sidecar file** for every file that had blocks removed, containing a full backup of the original content

#### ↩ Undo Clean

- Finds all `.log` sidecar files in the selected folder that contain a backup
- Restores each `.srt` file to its pre-cleaning state
- Deletes the `.log` file after a successful restore

#### 🔄 Subtitle Syncing (Alass)

- Select a video file and a misaligned subtitle
- Sub-Tools renames the original subtitle to `filename.ori.srt` (backup)
- Runs Alass to produce a new, perfectly timed `video_name.srt`
- All processing runs in a background thread — the GUI never freezes
- Success/error popup when done

#### 🔤 Ad Filter Keywords

- Click **🔤 Keywords** in the header to open the keyword editor
- Add, remove or reset the list of advertisement keywords
- Keywords are saved to `.env` and applied immediately

#### ⚙ Auto-Sync After Download

Check **"Auto-sync with Alass after download"** to enable a fully automatic pipeline:

```
Download → Clean (with user confirmation) → Alass Sync
```

This runs for every video in the selected folder in sequence.

---

### Configuration Reference (`.env`)

```env
MY_API_KEY=            # OpenSubtitles API key (required)
MY_USERNAME=           # Account username (optional)
MY_PASSWORD=           # Account password (optional)
MY_LANGUAGE=pt-br      # Default subtitle language code
AD_KEYWORDS_LIST=opensubtitles,vip,.com,...  # Ad filter words
SKIP_EXISTING=1        # 1 = skip videos with existing .srt
AUTO_SYNC=0            # 1 = auto-clean + auto-sync after each download
```

All values can be changed via the GUI (Settings ⚙ and Keywords 🔤 dialogs) — no manual file editing required.

---

### Supported Languages

| Language | Code |
|---|---|
| Portuguese Brazilian | `pt-br` |
| Portuguese | `pt` |
| English | `en` |
| Spanish | `es` |
| French | `fr` |
| German | `de` |
| Italian | `it` |
| Japanese | `ja` |
| Korean | `ko` |
| Chinese Simplified | `zh-cn` |
| Arabic | `ar` |
| Russian | `ru` |
| Dutch | `nl` |
| Polish | `pl` |
| Turkish | `tr` |

---

### Anonymous vs Authenticated Mode

| Mode | Requirement | Daily Download Limit |
|---|---|---|
| Anonymous | API key only | ~5 downloads / day |
| Authenticated | API key + username + password | Up to 200 downloads / day |

---

---

## 🇧🇷 Português

### O que é o Sub-Tools?

Sub-Tools é um utilitário desktop Python leve com interface gráfica moderna em tema escuro que automatiza três tarefas comuns relacionadas a legendas:

| Funcionalidade | O que faz |
|---|---|
| **⬇ Download de Legendas** | Busca no OpenSubtitles.com e baixa arquivos `.srt` para todos os vídeos de uma pasta |
| **✦ Limpeza de Legendas** | Remove tags HTML de formatação e blocos de propaganda dos arquivos `.srt` |
| **🔄 Sincronização** | Re-sincroniza um arquivo de legenda com seu vídeo usando o [Alass](https://github.com/kaegi/alass) |

---

### Interface

A janela do aplicativo é dividida em duas abas:

- **⬇ Download & Clean** — Selecione uma pasta, escolha o idioma e o Sub-Tools encontra e baixa todas as legendas ausentes. Opcionalmente, limpa e sincroniza automaticamente após cada download.
- **🔄 Subtitle Syncing** — Escolha um vídeo e uma legenda descalibrada, e o Alass re-sincroniza o tempo perfeitamente.

---

### Requisitos

| Dependência | Finalidade | Instalação |
|---|---|---|
| Python 3.9+ | Ambiente de execução | [python.org](https://python.org) |
| `opensubtitlescom` | Cliente da API REST do OpenSubtitles | `pip install opensubtitlescom` |
| `python-dotenv` | Carrega credenciais do `.env` | `pip install python-dotenv` |
| Alass _(opcional)_ | Motor de sincronização de legendas | Veja [Configuração do Alass](#configuração-do-alass) |

Instale as dependências Python com um único comando:

```bash
pip install opensubtitlescom python-dotenv
```

---

### Instalação

1. **Clone ou baixe** este repositório:
   ```bash
   git clone https://github.com/seu-usuario/sub-tools.git
   cd sub-tools
   ```

2. **Instale as dependências:**
   ```bash
   pip install opensubtitlescom python-dotenv
   ```

3. **Configure as credenciais** — crie um arquivo `.env` na pasta do projeto (ou use o diálogo Configurações dentro do app):
   ```env
   MY_API_KEY=sua_chave_api_opensubtitles
   MY_USERNAME=seu_usuario        # opcional — aumenta o limite diário de downloads
   MY_PASSWORD=sua_senha          # opcional — necessário se o usuário for preenchido
   ```
   > Obtenha uma chave de API gratuita em [opensubtitles.com/en/api](https://www.opensubtitles.com/en/api)

4. **Execute o aplicativo:**
   ```bash
   python legendaz.py
   ```

---

### Configuração do Alass

O [Alass](https://github.com/kaegi/alass) é uma ferramenta de linha de comando que re-sincroniza legendas automaticamente analisando a faixa de áudio do vídeo.

1. Baixe a versão mais recente para Windows na [página de releases do Alass](https://github.com/kaegi/alass/releases).
2. Extraia e coloque os arquivos dentro de uma pasta chamada `alass-windows64` **no mesmo diretório que o `legendaz.py`**:

```
sub-tools/
├── legendaz.py
├── .env
└── alass-windows64/
    ├── alass.bat          ← launcher recomendado
    └── alass.exe          ← executável
```

O Sub-Tools detecta o Alass automaticamente na inicialização e exibe um indicador verde na aba de Sincronização.

---

### Detalhes das Funcionalidades

#### ⬇ Download de Legendas

- Varre uma pasta **recursivamente** em busca de arquivos de vídeo (`.mp4`, `.mkv`, `.avi`, `.mov`, `.wmv`, `.flv`, `.m4v`, `.ts`)
- Busca no OpenSubtitles.com pelo nome do arquivo
- Baixa a melhor correspondência no idioma selecionado (com códigos de fallback)
- **15 idiomas** suportados, com preferência salva automaticamente no `.env`
- **Opção "Ignorar existentes"**: pula vídeos que já possuem arquivo de legenda
- Quando a opção está desativada, baixa a legenda com sufixo de idioma (ex: `filme.pt-br.srt`) para não sobrescrever

#### ✦ Limpeza de Legendas

- Varre todos os arquivos `.srt` da pasta selecionada recursivamente
- **Remove tags de formatação**: `<font>`, `<b>`, `<i>`, `<u>` e quaisquer outras tags HTML
- **Filtra blocos de propaganda**: blocos cujo texto contenha palavras-chave da lista configurável são sinalizados para remoção
- **Diálogo de confirmação**: antes de remover qualquer bloco, um modal exibe cada bloco sinalizado com seu texto, timestamp e palavra-chave detectada — o usuário marca/desmarca quais realmente deseja apagar (proteção contra falsos positivos)
- **Renumera** os blocos restantes sequencialmente para que o arquivo `.srt` permaneça válido
- **Gera um arquivo `.log` auxiliar** para cada arquivo alterado, contendo um backup completo do conteúdo original

#### ↩ Desfazer Limpeza

- Localiza todos os arquivos `.log` auxiliares na pasta selecionada que contenham um backup
- Restaura cada arquivo `.srt` ao estado anterior à limpeza
- Apaga o arquivo `.log` após a restauração bem-sucedida

#### 🔄 Sincronização de Legendas (Alass)

- Selecione um arquivo de vídeo e uma legenda descalibrada
- O Sub-Tools renomeia a legenda original para `arquivo.ori.srt` (backup)
- Executa o Alass para gerar uma nova legenda perfeitamente sincronizada com o nome `nome_do_video.srt`
- Todo o processamento ocorre em uma thread separada — a interface nunca trava
- Popup de sucesso ou erro ao finalizar

#### 🔤 Palavras-chave de Propaganda

- Clique em **🔤 Keywords** no cabeçalho para abrir o editor de palavras-chave
- Adicione, remova ou restaure os padrões da lista de palavras-chave
- As palavras são salvas no `.env` e aplicadas imediatamente

#### ⚙ Auto-Sincronização Após Download

Marque **"Auto-sync with Alass after download"** para ativar um pipeline totalmente automático:

```
Download → Limpar (com confirmação do usuário) → Sincronizar com Alass
```

Executa para cada vídeo da pasta selecionada em sequência.

---

### Referência de Configuração (`.env`)

```env
MY_API_KEY=            # Chave de API do OpenSubtitles (obrigatório)
MY_USERNAME=           # Nome de usuário da conta (opcional)
MY_PASSWORD=           # Senha da conta (opcional)
MY_LANGUAGE=pt-br      # Idioma padrão das legendas
AD_KEYWORDS_LIST=opensubtitles,vip,.com,...  # Palavras de filtro de propagandas
SKIP_EXISTING=1        # 1 = pular vídeos que já têm .srt
AUTO_SYNC=0            # 1 = limpar + sincronizar automaticamente após cada download
```

Todos os valores podem ser alterados pela interface gráfica (diálogos ⚙ Settings e 🔤 Keywords) — sem necessidade de editar o arquivo manualmente.

---

### Modo Anônimo vs Autenticado

| Modo | Requisito | Limite Diário de Downloads |
|---|---|---|
| Anônimo | Somente API key | ~5 downloads / dia |
| Autenticado | API key + usuário + senha | Até 200 downloads / dia |

---

### Estrutura do Projeto

```
sub-tools/
├── legendaz.py          # Código principal do aplicativo
├── .env                 # Credenciais e preferências (não versionar)
├── .env.example         # Template de configuração
├── README.md            # Esta documentação
└── alass-windows64/     # Pasta do executável Alass (opcional)
    ├── alass.bat
    └── alass.exe
```

---

### Licença / License

MIT License — free to use, modify and distribute.
