# anydesk-multi

Rode **várias instâncias isoladas do AnyDesk** em paralelo na mesma máquina
Windows — cada uma com seu próprio perfil e seu próprio ID — para atender
vários clientes ao mesmo tempo sem uma instalação derrubar a outra.

Ferramenta pequena, local e sem dependência de nuvem: nenhuma rede própria,
nenhuma telemetria, nenhum licenciamento, nenhuma senha armazenada. É só um
orquestrador de processos em cima do AnyDesk que você já tem instalado.

## Como funciona

O AnyDesk guarda o perfil (incluindo o **ID da máquina**, em `system.conf`)
dentro de `%APPDATA%\AnyDesk`. Lançando cada instância com as variáveis
`USERPROFILE` / `APPDATA` / `LOCALAPPDATA` / `HOMEPATH` redirecionadas para uma
pasta própria, cada "slot" ganha perfil e ID independentes e roda em paralelo.

Para o AnyDesk aceitar múltiplas instâncias e para conseguir identificar cada
processo depois, o executável é copiado uma vez por slot
(`anydesk_slot1.exe`, `anydesk_slot2.exe`, …) dentro do diretório de dados.

```
%LOCALAPPDATA%\AnyDeskMulti\
├─ bin\
│  ├─ anydesk_slot1.exe
│  └─ anydesk_slot2.exe ...
└─ slot2\profile\AppData\Roaming\AnyDesk\system.conf   (ID isolado do slot 2)
```

## Isolamento real: um binário por slot

Cópias **idênticas** do mesmo `AnyDesk.exe` compartilham o serviço/IPC do
AnyDesk na máquina: apenas a primeira instância sobe o `--local-service`, e as
demais o reaproveitam. Na prática, os slots rodam em paralelo, mas **encerrar o
slot que "dono" do serviço derruba os outros**.

Para encerramento realmente independente, dê a cada slot um **binário próprio**
(de preferência versões diferentes do AnyDesk). Coloque-os na pasta de fontes:

```
%LOCALAPPDATA%\AnyDeskMulti\sources\
├─ AnyDesk_Inst1.exe   (ex.: AnyDesk 6.3.3)
├─ AnyDesk_Inst2.exe   (ex.: AnyDesk 6.3.2)
├─ AnyDesk_Inst3.exe   (ex.: AnyDesk 6.3.0)
└─ AnyDesk_Inst4.exe   (ex.: AnyDesk 6.2.1)
```

O resto é automático: cada slot passa a ter seu próprio `--local-service`.
Confira a resolução com:

```bash
anydesk-multi sources
```

Ordem de resolução por slot: binário explícito (API `slot_binaries=`) →
`sources/AnyDesk_Inst<N>.exe` → `AnyDesk.exe` único (fallback). Use
`--sources-dir` para apontar outra pasta. Baixe versões antigas do AnyDesk em
<https://download.anydesk.com/>.

## Requisitos

- Windows
- [AnyDesk](https://anydesk.com/) instalado (ou um `AnyDesk.exe` portátil)
- Python 3.9+

> O AnyDesk **não** é redistribuído por este projeto. Você usa o executável que
> já tem, respeitando a licença do AnyDesk.

## Instalação

```bash
pip install -e .
```

Ou, sem instalar, use direto do diretório:

```bash
pip install -r requirements.txt
python -m anydesk_multi.cli --help
```

## Uso (CLI)

```bash
# Ver o que foi detectado (caminho do AnyDesk, pasta de dados, nº de slots)
anydesk-multi info

# Conectar a um ID remoto no primeiro slot livre
anydesk-multi connect 123456789

# Listar o estado de cada slot
anydesk-multi list

# Iniciar um slot específico (opcionalmente já conectando)
anydesk-multi launch 2
anydesk-multi launch 2 123456789

# Encerrar
anydesk-multi kill 2
anydesk-multi kill-all

# Limpar cache de um slot (preserva o ID)
anydesk-multi clean 2

# Apagar o perfil e gerar um ID novo para o slot
anydesk-multi clean 2 --new-id
```

Opções globais: `--exe <caminho>`, `--data-dir <pasta>`, `--slots <n>`,
`--low-priority`. O executável também pode ser apontado pela variável de
ambiente `ANYDESK_MULTI_EXE`.

## Painel gráfico (GUI)

Uma janela estilo painel: grade de cards por slot (estado, ID AnyDesk,
botões Abrir / Conectar / Encerrar / Limpar / Novo ID) e uma barra de
**Conexão Rápida** que conecta um ID no primeiro slot livre.

```bash
pip install -e .[gui]       # instala também o PyQt6
anydesk-multi-gui
```

Ou sem instalar:

```bash
pip install PyQt6
python -m anydesk_multi.gui
```

## Uso (como biblioteca)

```python
from anydesk_multi import AnyDeskManager

mgr = AnyDeskManager(max_slots=4)

ok, msg, slot = mgr.connect("123456789")
print(slot, msg)

for slot, st in mgr.scan().items():
    print(slot, st.running, st.formatted_id)

mgr.kill_all()
```

## Aviso de uso

Use apenas em máquinas e com clientes que você tem autorização para acessar.
Acesso remoto sem consentimento é ilegal na maioria das jurisdições. Esta
ferramenta apenas orquestra o AnyDesk; toda conexão continua sujeita às
aprovações e à política do próprio AnyDesk.

## Licença

[MIT](LICENSE).
