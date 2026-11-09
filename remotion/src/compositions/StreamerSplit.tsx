import React from "react";
import { AbsoluteFill } from "remotion";
import { BrollOverlays } from "../components/BrollOverlays";
import { Captions } from "../components/Captions";
import { SfxBadges } from "../components/SfxBadges";
import { SourceVideo } from "../components/SourceVideo";
import type { EditPlanProps } from "../types";
import { HEIGHT, WIDTH, normalizePlan } from "../types";

/**
 * StreamerSplit — o formato do vídeo alvo.
 *
 *   ┌────────────────┐
 *   │  NARRADOR 45%  │  fala (câmera/podcast/cara do streamer)
 *   ├────────────────┤
 *   │  GAMEPLAY 55%  │  jogo rodando, sem áudio (o áudio vem de cima)
 *   └────────────────┘
 *
 * As duas bandas vêm do MESMO vídeo de origem, em recortes diferentes:
 * a de cima enquadra o rosto (terço superior), a de baixo enquadra a ação
 * (centro). Isso é o que uma live tem: facecam + gameplay no mesmo frame.
 *
 * Se `background_video_path` apontar para outro arquivo, ele é usado na banda
 * inferior — serve para quando a fala e o gameplay vêm de fontes separadas.
 *
 * O áudio vem **só** da banda de cima (`muted` na de baixo). Duas trilhas
 * tocando ao mesmo tempo seria eco, não edição.
 */
export const StreamerSplit: React.FC<EditPlanProps> = (props) => {
  const plan = normalizePlan(props);

  const narratorHeight = Math.round(HEIGHT * plan.narratorSplit);
  const gameplayHeight = HEIGHT - narratorHeight;
  const hasSeparateBackground = Boolean(plan.backgroundVideoPath);

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {/* ---- banda do narrador (áudio principal) ---------------------- */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: WIDTH,
          height: narratorHeight,
          overflow: "hidden",
          backgroundColor: "#000",
        }}
      >
        <SourceVideo
          src={plan.sourceVideoPath}
          // Enquadra o terço superior: é onde fica a facecam numa live.
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            objectPosition: "center 12%",
            filter: "contrast(1.04) saturate(1.05)",
          }}
        />
        {/* Sombra inferior: separa as bandas sem linha dura. */}
        <AbsoluteFill
          style={{
            background:
              "linear-gradient(180deg, rgba(0,0,0,0.28) 0%, rgba(0,0,0,0) 30%, rgba(0,0,0,0) 70%, rgba(0,0,0,0.5) 100%)",
          }}
        />
        <div
          style={{
            position: "absolute",
            top: 22,
            left: 28,
            padding: "6px 16px",
            borderRadius: 8,
            backgroundColor: "rgba(0,0,0,0.45)",
            color: "rgba(255,255,255,0.85)",
            fontFamily: "'Arial Black', system-ui, sans-serif",
            fontWeight: 900,
            fontSize: 26,
            letterSpacing: 3,
            textTransform: "uppercase",
          }}
        >
          {plan.watermark}
        </div>
      </div>

      {/* ---- banda do gameplay (mudo) --------------------------------- */}
      <div
        style={{
          position: "absolute",
          top: narratorHeight,
          left: 0,
          width: WIDTH,
          height: gameplayHeight,
          overflow: "hidden",
          backgroundColor: "#000",
        }}
      >
        {hasSeparateBackground ? (
          <SourceVideo
            src={plan.backgroundVideoPath as string}
            muted
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              objectPosition: "center center",
            }}
          />
        ) : (
          <SourceVideo
            src={plan.sourceVideoPath}
            muted
            // Enquadra o centro: é onde o gameplay acontece numa live.
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              objectPosition: "center 62%",
            }}
          />
        )}
        <AbsoluteFill
          style={{
            background:
              "linear-gradient(180deg, rgba(0,0,0,0.35) 0%, rgba(0,0,0,0) 18%)",
          }}
        />
      </div>

      {/* Linha de corte entre as bandas */}
      <div
        style={{
          position: "absolute",
          top: narratorHeight - 2,
          left: 0,
          width: WIDTH,
          height: 4,
          backgroundColor: "rgba(255,255,255,0.85)",
        }}
      />

      {/* ---- sobreposições ------------------------------------------- */}
      <BrollOverlays overlays={plan.broll} />
      <Captions
        words={plan.words}
        bottom={gameplayHeight - 90}
        fontSize={64}
        accentColor="#FFFFFF"
      />
      <SfxBadges plan={plan} top={narratorHeight + 110} />

    </AbsoluteFill>
  );
};
