"""Repositório de manutenções em memória, para os testes. Nada acessa o Supabase.

Imita as garantias da função do banco: usuário válido (PT403), manutenção existente (PT404) e
dados inválidos (PT422). Falhas de comunicação podem ser simuladas com `falhar_em`.
"""

from datetime import datetime
from decimal import Decimal

from app.manutencao_repositorio import Manutencao, ManutencaoRecusada

USUARIOS = {1: "Leandro", 2: "Kassia", 3: "Marise"}


class RepositorioManutencaoMemoria:
    def __init__(self):
        self.manutencoes: list[Manutencao] = []
        self.proximo_id = 1
        self.falhar_em: dict[str, Exception] = {}  # operação -> erro a levantar
        self.gravacoes: list[tuple] = []  # (id, valores, usuário) de cada gravação aceita
        self.leituras = 0

    def _falhar(self, operacao: str):
        if operacao in self.falhar_em:
            raise self.falhar_em[operacao]

    def listar(self) -> list[Manutencao]:
        self._falhar("listar")
        self.leituras += 1
        return sorted(self.manutencoes, key=lambda m: (m.data, m.id), reverse=True)

    def salvar(self, manutencao_id: int | None, valores: dict, usuario_id: int) -> Manutencao:
        self._falhar("salvar")
        if usuario_id not in USUARIOS:
            raise ManutencaoRecusada("usuario")
        if (
            valores["tipo"] not in ("preventiva", "corretiva")
            or not 0 <= valores["minutos"] <= 525600
            or valores["troca_pecas"] != (valores["pecas"] is not None)
            or (valores["custo"] is not None and valores["custo"] < Decimal(0))
        ):
            raise ManutencaoRecusada("invalido")
        agora = datetime(2026, 11, 2, 10, 0)
        anterior = None
        if manutencao_id is not None:
            anterior = next((m for m in self.manutencoes if m.id == manutencao_id), None)
            if anterior is None:
                raise ManutencaoRecusada("nao_encontrado")
        nova = Manutencao(
            id=manutencao_id if manutencao_id is not None else self.proximo_id,
            data=valores["data"],
            tipo=valores["tipo"],
            minutos_parados=valores["minutos"],
            troca_pecas=valores["troca_pecas"],
            pecas=valores["pecas"],
            custo=valores["custo"],
            comentarios=valores["comentarios"],
            legado=anterior.legado if anterior else False,
            criado_por=anterior.criado_por if anterior else USUARIOS[usuario_id],
            criado_em=anterior.criado_em if anterior else agora,
            atualizado_por=USUARIOS[usuario_id],
            atualizado_em=agora,
        )
        if anterior is None:
            self.proximo_id += 1
            self.manutencoes.append(nova)
        else:
            self.manutencoes[self.manutencoes.index(anterior)] = nova
        self.gravacoes.append((nova.id, dict(valores), usuario_id))
        return nova
