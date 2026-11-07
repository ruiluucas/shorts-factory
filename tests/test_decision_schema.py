"""Testes dos contratos da camada de decisão e da validação de config.

Estes testes NÃO tocam a rede. Nenhum deles precisa de chave de API.
"""

from __future__ import annotations

import pytest

from core.decision_config import (
    DecisionConfig,
    DecisionConfigError,
    load_decision_config,
    parse_decision_config,
    validate_weights_sum,
)
from core.schemas import (
    BackendKind,
    DecisionKind,
    DecisionOption,
    DecisionRequest,
    DecisionResult,
    GateResult,
    GateVerdict,
    RegionOfInterest,
)

# --------------------------------------------------------------- schemas


@pytest.mark.scoring
def test_decision_request_validates_choice_options():
    req = DecisionRequest(
        kind=DecisionKind.CHOICE,
        state="texto",
        options=[DecisionOption(name="a", description="opção a"), DecisionOption(name="b")],
    )
    assert req.validate_kind() is req


@pytest.mark.scoring
def test_choice_without_options_is_rejected():
    req = DecisionRequest(kind=DecisionKind.CHOICE, state="x")
    with pytest.raises(ValueError, match="ao menos uma opção"):
        req.validate_kind()


@pytest.mark.scoring
def test_score_rejects_more_than_ten_levels():
    req = DecisionRequest(
        kind=DecisionKind.SCORE,
        options=[DecisionOption(name=str(i)) for i in range(11)],
    )
    with pytest.raises(ValueError, match="entre 1 e 10"):
        req.validate_kind()


@pytest.mark.scoring
def test_box_rejects_options():
    req = DecisionRequest(kind=DecisionKind.BOX, options=[DecisionOption(name="a")])
    with pytest.raises(ValueError, match="box não aceita opções"):
        req.validate_kind()


@pytest.mark.scoring
def test_decision_result_confidence_gate():
    """Abaixo do limiar, o chamador deve tratar como indisponível."""
    low = DecisionResult(backend=BackendKind.JEV_API, kind=DecisionKind.NOUL, noul=0.9, confidence=0.3)
    high = DecisionResult(backend=BackendKind.JEV_API, kind=DecisionKind.NOUL, noul=0.9, confidence=0.9)

    assert low.is_confident(0.55) is False
    assert high.is_confident(0.55) is True


@pytest.mark.scoring
def test_decision_result_default_backend_is_explicit():
    """Todo resultado registra quem decidiu — nunca fica implícito."""
    r = DecisionResult(backend=BackendKind.HEURISTIC, kind=DecisionKind.SCORE, score=1.0)
    assert r.backend is BackendKind.HEURISTIC
    assert r.fell_back is False


@pytest.mark.scoring
def test_gate_result_triggered_flags():
    gate = GateResult(
        verdict=GateVerdict.BLOCK,
        flags={"profanity": 0.94, "violence": 0.92, "medical_advice": 0.09},
        confidence=0.84,
        reasoned_by="profanity",
    )
    triggered = gate.triggered(0.60)
    assert triggered == ["profanity", "violence"]
    assert "medical_advice" not in triggered


@pytest.mark.scoring
def test_roi_validates_and_centers():
    roi = RegionOfInterest(
        t_start=1.0,
        t_end=2.0,
        bbox_norm=(0.2, 0.4, 0.6, 0.8),
        label="carro",
        source="heuristic",
        confidence=0.7,
    )
    roi.validate_box()
    assert roi.center == pytest.approx((0.4, 0.6))


@pytest.mark.scoring
def test_roi_rejects_inverted_box():
    bad = RegionOfInterest(
        t_start=0,
        t_end=1,
        bbox_norm=(0.6, 0.4, 0.2, 0.8),
        label="x",
        source="heuristic",
    )
    with pytest.raises(ValueError, match="bbox inválida"):
        bad.validate_box()


@pytest.mark.scoring
def test_roi_rejects_out_of_range():
    bad = RegionOfInterest(
        t_start=0,
        t_end=1,
        bbox_norm=(0.0, 0.0, 1.4, 1.0),
        label="x",
        source="heuristic",
    )
    with pytest.raises(ValueError, match="bbox inválida"):
        bad.validate_box()


# --------------------------------------------------------------- config


@pytest.mark.scoring
def test_default_config_is_offline_and_all_uses_off():
    """Padrão de fábrica: nada remoto, nenhum uso ligado."""
    cfg = DecisionConfig()
    assert cfg.backend == "heuristic"
    assert cfg.allow_remote is False
    assert cfg.is_remote is False
    assert cfg.uses.enabled() == []
    assert cfg.any_use_enabled() is False


@pytest.mark.scoring
def test_shipped_yaml_loads_and_is_safe():
    """O YAML versionado precisa carregar e manter a postura offline."""
    cfg = load_decision_config()
    assert cfg.backend == "heuristic"
    assert cfg.allow_remote is False
    assert cfg.uses.enabled() == [], f"usos ligados por padrão: {cfg.uses.enabled()}"
    assert cfg.visual.send_frames_remote is False


@pytest.mark.scoring
def test_remote_backend_without_allow_remote_is_rejected():
    """Trava de privacidade: backend remoto sem permissão é config inválida."""
    with pytest.raises(DecisionConfigError, match=r"exige decision\.allow_remote: true"):
        parse_decision_config({"backend": "jev_api", "allow_remote": False})


@pytest.mark.scoring
def test_remote_frames_without_allow_remote_is_rejected():
    with pytest.raises(DecisionConfigError, match="send_frames_remote"):
        parse_decision_config({"visual": {"send_frames_remote": True}, "allow_remote": False})


@pytest.mark.scoring
def test_remote_backend_with_allow_remote_is_accepted():
    cfg = parse_decision_config({"backend": "jev_api", "allow_remote": True})
    assert cfg.is_remote is True


@pytest.mark.scoring
def test_unknown_backend_rejected():
    with pytest.raises(DecisionConfigError, match="desconhecido"):
        parse_decision_config({"backend": "gpt5"})


@pytest.mark.scoring
def test_openjev_requires_base_url():
    with pytest.raises(DecisionConfigError, match="openjev_base_url"):
        parse_decision_config({"backend": "openjev", "allow_remote": True})


@pytest.mark.scoring
def test_videojevv_grounding_requires_endpoint():
    """VideoJev não cabe local; ligar sem endpoint é erro de configuração."""
    with pytest.raises(DecisionConfigError, match="videojev_enabled"):
        parse_decision_config({"uses": {"video_grounding": True}})


@pytest.mark.scoring
def test_videojev_enabled_requires_endpoint():
    with pytest.raises(DecisionConfigError, match="videojev_endpoint"):
        parse_decision_config({"videojev_enabled": True})


@pytest.mark.scoring
def test_videojev_full_config_accepted():
    cfg = parse_decision_config(
        {
            "allow_remote": True,
            "videojev_enabled": True,
            "videojev_endpoint": "https://gpu.example/v1",
            "uses": {"video_grounding": True},
        }
    )
    assert cfg.uses.video_grounding is True
    assert cfg.videojev_endpoint == "https://gpu.example/v1"


@pytest.mark.scoring
def test_backoff_ordering_validated():
    with pytest.raises(DecisionConfigError, match="max_backoff_s"):
        parse_decision_config({"backoff_base_s": 10.0, "max_backoff_s": 1.0})


@pytest.mark.scoring
def test_min_confidence_bounds():
    with pytest.raises(DecisionConfigError):
        parse_decision_config({"min_confidence": 1.5})


@pytest.mark.scoring
def test_missing_decision_block_is_not_an_error():
    """Pipeline antigo sem bloco `decision` continua rodando."""
    cfg = parse_decision_config(None)
    assert cfg.backend == "heuristic"
    assert cfg.allow_remote is False


@pytest.mark.scoring
def test_weights_must_sum_to_one():
    validate_weights_sum({"a": 0.5, "b": 0.5})
    with pytest.raises(DecisionConfigError, match=r"deveriam somar 1\.0"):
        validate_weights_sum({"a": 0.5, "b": 0.4})


@pytest.mark.scoring
def test_weights_tolerance_accepts_float_noise():
    validate_weights_sum({"a": 1 / 3, "b": 1 / 3, "c": 1 / 3})


@pytest.mark.scoring
def test_publish_gate_flags_unique():
    with pytest.raises(DecisionConfigError, match="duplicadas"):
        parse_decision_config({"publish_gate": {"flags": ["profanity", "profanity"]}})
