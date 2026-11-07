import React from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { Captions } from "../components/Captions";
import { SfxBadges } from "../components/SfxBadges";
import { SourceVideo } from "../components/SourceVideo";
import type { EditPlanProps, NormalizedPlan } from "../types";
import { HEIGHT, WIDTH, normalizePlan } from "../types";

/** gameplay occupies the top ~72% of the frame, facecam the bottom band */
export const GAMEPLAY_HEIGHT = Math.round(HEIGHT * 0.72);
export const FACECAM_HEIGHT = HEIGHT - GAMEPLAY_HEIGHT;

export type ShortSplitScreenProps = EditPlanProps & {
  /** zoom factor applied to the gameplay panel (simulates a crop) */
  gameplayZoom?: number;
  showTitle?: boolean;
};

const Panel: React.FC<{
  src: string;
  cropTopPercent: number;
  cropHeightPercent: number;
  zoom: number;
}> = ({ src, cropTopPercent, cropHeightPercent, zoom }) => (
  <AbsoluteFill style={{ overflow: "hidden", backgroundColor: "#000" }}>
    <SourceVideo
      src={src}
      style={{
        width: "100%",
        height: "100%",
        objectFit: "cover",
        objectPosition: "center",
        transform: `scale(${zoom})`,
        // shift the source window vertically without re-encoding:
        // we rely on object-fit cover + a vertical offset via translate.
        // cropTopPercent is applied through the container's top margin below.
      }}
      data-crop-top={cropTopPercent}
      data-crop-height={cropHeightPercent}
    />
  </AbsoluteFill>
);

/**
 * ShortSplitScreen — the default vertical layout.
 *
 *   ┌───────────────┐
 *   │   GAMEPLAY    │  (16:10-ish, large)
 *   ├───────────────┤
 *   │   FACECAM     │  (letterboxed band)
 *   └───────────────┘
 *
 * + karaoke captions and optional SFX badges.
 */
export const ShortSplitScreen: React.FC<ShortSplitScreenProps> = (props) => {
  const plan: NormalizedPlan = normalizePlan(props);
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  const gameplayZoom = props.gameplayZoom ?? 1.06;
  const showTitle = props.showTitle ?? true;

  // gentle push-in over the whole clip for a livelier edit
  const drift = interpolate(frame, [0, durationInFrames], [1, 1.04], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: "#08080C" }}>
      {/* --- gameplay panel --------------------------------------------- */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: WIDTH,
          height: GAMEPLAY_HEIGHT,
          overflow: "hidden",
          borderBottom: "6px solid #FFE600",
        }}
      >
        <div
          style={{
            position: "absolute",
            inset: 0,
            transform: `scale(${gameplayZoom * drift})`,
          }}
        >
          <SourceVideo
            src={plan.sourceVideoPath}
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              objectPosition: "center",
            }}
          />
        </div>
        <AbsoluteFill
          style={{
            background:
              "linear-gradient(180deg, rgba(0,0,0,0.35) 0%, rgba(0,0,0,0) 25%, rgba(0,0,0,0) 75%, rgba(0,0,0,0.45) 100%)",
          }}
        />
        {showTitle ? (
          <div
            style={{
              position: "absolute",
              top: 28,
              left: 28,
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "8px 18px",
              borderRadius: 10,
              backgroundColor: "rgba(255,59,48,0.92)",
              color: "#fff",
              fontFamily: "'Arial Black', system-ui, sans-serif",
              fontWeight: 900,
              fontSize: 30,
              letterSpacing: 2,
              textTransform: "uppercase",
            }}
          >
            <span
              style={{
                width: 14,
                height: 14,
                borderRadius: 7,
                backgroundColor: "#fff",
                opacity: 0.5 + 0.5 * Math.sin(frame / 4),
              }}
            />
            {plan.watermark ?? "SHORTS"}
          </div>
        ) : null}
      </div>

      {/* --- facecam band ----------------------------------------------- */}
      <div
        style={{
          position: "absolute",
          top: GAMEPLAY_HEIGHT,
          left: 0,
          width: WIDTH,
          height: FACECAM_HEIGHT,
          overflow: "hidden",
          backgroundColor: "#000",
        }}
      >
        {/* In a split-screen source the facecam is usually in the lower
            region of the frame; we show that region here. */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            transform: "scale(1.35) translateY(-2%)",
            transformOrigin: "center top",
          }}
        >
          <SourceVideo
            src={plan.sourceVideoPath}
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              objectPosition: "center 88%",
              filter: "contrast(1.05) saturate(1.05)",
            }}
          />
        </div>
        <AbsoluteFill
          style={{
            background:
              "linear-gradient(0deg, rgba(8,8,12,0.92) 0%, rgba(8,8,12,0.25) 45%, rgba(8,8,12,0) 100%)",
          }}
        />
      </div>

      {/* --- captions + sfx --------------------------------------------- */}
      <Captions words={plan.words} bottom={FACECAM_HEIGHT - 40} />
      <SfxBadges plan={plan} top={GAMEPLAY_HEIGHT * 0.62} />
    </AbsoluteFill>
  );
};