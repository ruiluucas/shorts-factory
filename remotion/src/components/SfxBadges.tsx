import React from "react";
import {
  AbsoluteFill,
  Sequence,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type { NormalizedPlan } from "../types";

type Props = {
  plan: NormalizedPlan;
  /** vertical center of the badge, px from top; defaults near the top third */
  top?: number;
};

const BADGE_COLORS = ["#FF3B30", "#FFE600", "#00E5A0", "#7C6BFF", "#FF8A00"];

const pickColor = (s: string) => {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) % 100000;
  }
  return BADGE_COLORS[h % BADGE_COLORS.length];
};

/**
 * Renders a short-lived, springy emoji/text badge for every SFX trigger in
 * the edit plan. Purely decorative: the audio itself is carried by the source
 * video track (the pipeline never injects commercial music).
 */
export const SfxBadges: React.FC<Props> = ({ plan, top = 420 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  if (plan.sfx.length === 0) {
    return null;
  }

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {plan.sfx.map((trigger, idx) => {
        const startFrame = Math.round(trigger.time * fps);
        const durationFrames = Math.round(0.9 * fps);
        const label = (trigger.label ?? trigger.asset).replace(
          /\.[a-z0-9]+$/i,
          "",
        );

        if (frame < startFrame || frame > startFrame + durationFrames) {
          return null;
        }

        const local = frame - startFrame;
        const scale = interpolate(
          local,
          [0, 6, durationFrames],
          [0.6, 1.08, 0.95],
          { extrapolateRight: "clamp", extrapolateLeft: "clamp" },
        );
        const opacity = interpolate(
          local,
          [0, 4, durationFrames - 8, durationFrames],
          [0, 1, 1, 0],
          { extrapolateRight: "clamp", extrapolateLeft: "clamp" },
        );
        const rotate = interpolate(local, [0, durationFrames], [-8, 8]);

        return (
          <Sequence
            key={`${trigger.asset}-${trigger.time}-${idx}`}
            from={startFrame}
            durationInFrames={durationFrames}
          >
            <AbsoluteFill
              style={{
                alignItems: "center",
                justifyContent: "flex-start",
                paddingTop: top + (idx % 3) * 90,
              }}
            >
              <div
                style={{
                  transform: `scale(${scale}) rotate(${rotate}deg)`,
                  opacity,
                  backgroundColor: pickColor(trigger.asset),
                  color: "#08080C",
                  fontFamily: "'Arial Black', system-ui, sans-serif",
                  fontWeight: 900,
                  fontSize: 46,
                  textTransform: "uppercase",
                  letterSpacing: 1.5,
                  padding: "10px 26px",
                  borderRadius: 14,
                  border: "5px solid #08080C",
                  boxShadow: "0 10px 0 rgba(0,0,0,0.35)",
                  whiteSpace: "nowrap",
                }}
              >
                {label}
              </div>
            </AbsoluteFill>
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
