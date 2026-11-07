#!/usr/bin/env python3
"""Gera as fixtures de vídeo sintéticas do harness.

Três perfis, cada um exercitando uma parte do pipeline:

  (a) fala_continua.mp4  — fala corrida, sem picos. Baseline "sem graça":
      o scorer deve achar pouca coisa viral aqui.
  (b) fala_reativa.mp4   — fala com rajadas de reação e picos de energia.
      É onde o scorer DEVE encontrar os melhores momentos.
  (c) cena_passiva.mp4   — quase estático, sem rosto, sem fala.
      DEVE ser penalizado (passive_gameplay_cutscene, -40).

Nenhum binário é commitado: tudo é gerado sob demanda por ffmpeg + TTS.

Uso:
    python tests/fixtures/make_fixtures.py            # gera o que falta
    python tests/fixtures/make_fixtures.py --force    # regenera tudo
    python tests/fixtures/make_fixtures.py --list     # só lista

O TTS usa `piper` via `uv tool run` (voz pt_BR-faber-medium, ~63 MB, baixada
uma vez para /tmp). Se piper não estiver disponível, cai para tons de áudio:
as fixtures continuam válidas para visão/áudio, mas não para o scorer léxico.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent
VOICE_DIR = Path("/tmp/shorts-voice")
VOICE_NAME = "pt_BR-faber-medium"
VOICE_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium"

WIDTH, HEIGHT, FPS = 1080, 1920, 30

# ---------------------------------------------------------------- roteiros

SCRIPT_CONTINUOUS = """
Hoje eu vou mostrar como funciona o sistema de crafting nesse jogo.
Primeiro você precisa coletar madeira nas árvores próximas da vila.
Depois abre o inventário e combina os materiais na bancada de trabalho.
O resultado é uma ferramenta básica que ajuda bastante no começo.
Com o tempo você vai desbloqueando receitas mais avançadas.
Vale a pena investir tempo aprendendo a mecânica desde o início.
"""

SCRIPT_REACTIVE = """
Mano, olha isso, caralho, o carro voou na minha cabeça.
Não acredito, meu deus, que queda foi essa, sério mesmo.
Olha o carro, olha o carro, não, não, não, ele vai bater.
Caralho, acertou em cheio, que isso, que absurdo, não acredito.
Eu nunca vi nada igual, mano, isso foi surreal demais.
Olha o tamanho do estrago, meu deus do céu, que loucura.
Não acredito que ele fez isso de novo, caralho, que isso.
Ele tá voando de novo, olha isso, olha isso, meu deus.
"""

# ---------------------------------------------------------------- util


def log(msg: str) -> None:
    print(f"  {msg}")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def have(binary: str) -> bool:
    return shutil.which(binary) is not None


# ---------------------------------------------------------------- voz


def ensure_voice() -> tuple[Path, Path] | None:
    """Baixa o modelo piper uma vez. Retorna (modelo, config) ou None."""
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    model = VOICE_DIR / f"{VOICE_NAME}.onnx"
    cfg = VOICE_DIR / f"{VOICE_NAME}.onnx.json"

    for path, suffix in ((model, ".onnx"), (cfg, ".onnx.json")):
        if path.exists() and path.stat().st_size > 1000:
            continue
        url = f"{VOICE_BASE}/{VOICE_NAME}{suffix}"
        log(f"baixando {path.name}…")
        try:
            urllib.request.urlretrieve(url, path)
        except Exception as exc:  # rede pode falhar de qualquer jeito
            log(f"falha ao baixar voz: {exc}")
            return None

    return model, cfg


def synth_speech(text: str, out_wav: Path) -> bool:
    """Sintetiza fala PT-BR com piper. False se indisponível."""
    voice = ensure_voice()
    if voice is None:
        return False
    model, _ = voice

    try:
        subprocess.run(
            ["uv", "tool", "run", "--from", "piper-tts", "piper", "-m", str(model), "-f", str(out_wav)],
            input=text,
            text=True,
            check=True,
            capture_output=True,
            timeout=300,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        log(f"piper falhou ({exc.__class__.__name__}) — usando tons")
        return False

    return out_wav.exists() and out_wav.stat().st_size > 1000


# ---------------------------------------------------------------- vídeo


def make_video(visual: str, audio: str, out: Path, duration: float) -> None:
    """Monta um MP4 vertical H.264 + AAC a partir de fontes lavfi/arquivo."""
    video_src = {
        # padrão de testes em movimento: motion alto, sem rosto
        "motion": f"testsrc2=size={WIDTH}x{HEIGHT}:rate={FPS}:duration={duration}",
        # fundo quase estático: motion baixo => cena passiva
        "static": f"color=c=0x101018:size={WIDTH}x{HEIGHT}:rate={FPS}:duration={duration}",
    }[visual]

    if audio == "silence":
        audio_src = ["-f", "lavfi", "-i", f"anullsrc=r=16000:cl=mono:d={duration}"]
        acodec = ["-c:a", "aac"]
    elif audio == "tone":
        audio_src = ["-f", "lavfi", "-i", f"sine=frequency=220:duration={duration}"]
        acodec = ["-c:a", "aac"]
    else:  # arquivo wav
        audio_src = ["-i", audio]
        acodec = ["-c:a", "aac"]

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        video_src,
        *audio_src,
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-pix_fmt",
        "yuv420p",
        *acodec,
        "-shortest",
        "-t",
        str(duration),
        str(out),
    ]
    run(cmd)


def build_continuous(out: Path) -> None:
    """(a) Fala corrida, sem emoção — baseline baixo de viralidade."""
    duration = 40.0
    wav = out.with_suffix(".wav")
    if not synth_speech(SCRIPT_CONTINUOUS.strip(), wav):
        make_video("motion", "tone", out, duration)
        return
    make_video("motion", str(wav), out, duration)
    wav.unlink(missing_ok=True)


def build_reactive(out: Path) -> None:
    """(b) Fala com reações fortes — onde o scorer deve acertar."""
    duration = 30.0
    wav = out.with_suffix(".wav")
    if not synth_speech(SCRIPT_REACTIVE.strip(), wav):
        # fallback: padrão de rajadas (não é fala, mas gera picos reais)
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={WIDTH}x{HEIGHT}:rate={FPS}:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            (f"aevalsrc='if(lt(mod(t,3),0.4),0.9*sin(2*PI*440*t),0.01*sin(2*PI*220*t))':s=16000:d={duration}"),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ]
        run(cmd)
        return
    make_video("motion", str(wav), out, duration)
    wav.unlink(missing_ok=True)


def build_passive(out: Path) -> None:
    """(c) Cena parada, sem rosto nem fala — deve ser penalizada."""
    make_video("static", "silence", out, 20.0)


# ---------------------------------------------------------------- main

BUILDERS = {
    "fala_continua": build_continuous,
    "fala_reativa": build_reactive,
    "cena_passiva": build_passive,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="regenera mesmo se existir")
    ap.add_argument("--list", action="store_true", help="só lista o que seria gerado")
    args = ap.parse_args()

    if not have("ffmpeg"):
        print("erro: ffmpeg não encontrado no PATH", file=sys.stderr)
        return 1

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    print(f"fixtures em {FIXTURES_DIR}")

    for name, builder in BUILDERS.items():
        out = FIXTURES_DIR / f"{name}.mp4"
        if args.list:
            estado = "existe" if out.exists() else "ausente"
            print(f"  {name:16s} {estado}")
            continue
        if out.exists() and not args.force:
            print(f"  {name:16s} já existe — pulando")
            continue
        print(f"  {name:16s} gerando…")
        builder(out)
        size_mb = out.stat().st_size / 1e6 if out.exists() else 0
        print(f"  {name:16s} ok ({size_mb:.1f} MB)")

    if not args.list:
        print("\nfeito. estes arquivos NÃO são versionados (.gitignore).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
