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

/** thick letterbox bars, in px of a 1920px tall frame */
export const BAR_HEIGHT = Math.round(HEIGHT * 0.11);

export type GTACinematicProps = EditPlanProps & {
  /** black bar height as a fraction of the frame height (default 0.11) */
  barRatio?: number;
  /** optional vignette strength 0..1 */
  vignette?: number;
  zoom?: number;
};

/**
 * GTACinematic — centered source footage with cinematic black bars and
 * karaoke captions sitting just above the bottom bar.
 */
export const GTACinematic: React.FC<GTACinematicProps> = (props) => {
  const plan: NormalizedPlan = normalizePlan(props);
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  const barRatio = props.barRatio ?? 0.11;
  const bar = Math.round(HEIGHT * barRatio);
  const vignette = props.vignette ?? 0.45;
  const baseZoom = props.zoom ?? 1.0;

  const drift = interpolate(frame, [0, durationInFrames], [1, 1.03], {
    extrapolateRight: "clamp",
  });
  const fadeIn = interpolate(frame, [0, 12], [0, 1], {
    extrapolateRight: "clamp",
    extrapolateLeft: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      <AbsoluteFill style={{ opacity: fadeIn }}>
        <AbsoluteFill
          style={{ transform: `scale(${baseZoom * drift})`, inset: 0 }}
        >
          <SourceVideo
            src={plan.sourceVideoPath}
            style={{
              width: WIDTH,
              height: HEIGHT,
              objectFit: "cover",
              objectPosition: "center",
            }}
          />
        </AbsoluteFill>
      </AbsoluteFill>

      {/* vignette */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(ellipse at center, rgba(0,0,0,0) 45%, rgba(0,0,0,${vignette}) 100%)`,
        }}
      />

      {/* cinematic bars */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: WIDTH,
          height: bar,
          backgroundColor: "#000",
        }}
      />
      <div
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          width: WIDTH,
          height: bar,
          backgroundColor: "#000",
        }}
      />

      {/* top bar wordmark */}
      <div
        style={{
          position: "absolute",
          top: bar / 2 - 22,
          left: 0,
          width: WIDTH,
          textAlign: "center",
          color: "rgba(255,255,255,0.55)",
          fontFamily: "'Arial Black', system-ui, sans-serif",
          fontWeight: 900,
          fontSize: 34,
          letterSpacing: 6,
          textTransform: "uppercase",
        }}
      >
        GTA
      </div>

      <Captions
        words={plan.words}
        bottom={bar + 90}
        fontSize={68}
        accentColor="#FFFFFF"
      />
      <SfxBadges plan={plan} top={bar + 120} />
    </AbsoluteFill>
  );
};