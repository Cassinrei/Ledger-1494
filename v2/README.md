# v2 — o ledger que não mente

Mantém tudo da v1 (contas, partidas dobradas, soma zero, saldo derivado, centavos) e acrescenta três garantias.

## 1. Imutabilidade — nunca editar, nunca apagar

- O `Ledger` não tem nenhum método de edição ou exclusão. O diário só cresce.
- `JournalEntry` e `Posting` são congelados (`frozen=True`, partidas em tupla); `journal` é exposto como tupla e `accounts` como mapeamento somente-leitura.
- **Cadeia de hashes**: cada lançamento guarda `prev_hash` e o próprio `hash` (SHA-256 do conteúdo + hash anterior). `verify()` recalcula a cadeia e acusa `TamperedLedger` se alguém alterar, remover ou reordenar o histórico por baixo dos panos. `head` é a impressão digital do livro inteiro.

## 2. Idempotência por chave de negócio

Todo lançamento exige uma `key` vinda do negócio (`"NF-0001"`, `"pix:E123…"`, `"pedido-42"`).

| Situação | Resultado |
|---|---|
| key nova | registra |
| mesma key, mesmo conteúdo (retry) | devolve o lançamento existente, não duplica |
| mesma key, conteúdo diferente | `IdempotencyConflict` |

"Mesmo conteúdo" = mesma data, histórico e partidas (a ordem das partidas não importa).

## 3. Estorno por lançamento compensatório

```python
L.post("pag-0001", date(1494, 1, 10), "Pagamento", [D("2.1", "50"), C("1.1", "50")])
L.reverse("pag-0001", date(1494, 1, 11), "valor incorreto")   # key: "estorno:pag-0001"
L.post("pag-0002", date(1494, 1, 12), "Pagamento", [D("2.1", "400"), C("1.1", "400")])
```

- O estorno é um **novo** lançamento com os sinais invertidos, apontando para o original (`reverses`). O original continua no diário, e o saldo em datas passadas continua contando a história verdadeira.
- Cada lançamento só pode ser estornado uma vez; um estorno não pode ser estornado (para refazer, lance um novo fato com nova key).
- O próprio estorno é idempotente: repetir `reverse` com os mesmos dados devolve o mesmo estorno.

## Rodar

```bash
cd v2
python3 ledger.py               # demo: retry, conflito, estorno, adulteração detectada
python3 -m unittest test_ledger # 19 testes
```
