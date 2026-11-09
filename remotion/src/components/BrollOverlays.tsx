import React from "react";
import { Img, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { resolveSrc } from "./SourceVideo";
import type { BrollOverlay } from "../types";
import { HEIGHT, WIDTH } from "../types";

/**
 * B-roll overlay: an image illustrating what is being said right now.
 *
 * Placement rules (why "top"):
 *  - captions live in the lower third, so the image must not fight them;
 *  - the top band is the safest area on Shorts/TikTok against UI chrome;
 *  - the image fades in and out, so a hard cut never appears.
 *
 * The image is scaled to fit a max width, keeping its aspect ratio. If the
 * file cannot be loaded the component renders nothing rather than a broken
 * box — an overlay must never ruin an otherwise good short.
 */
const FADE_FRAMES = 8;

export const BrollOverlays: React.FC<{ overlays: BrollOverlay[] }> = ({
  overlays,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;

  const active = overlays.filter((o) => t >= o.t_start && t <= o.t_end);
  if (active.length === 0) {
    return null;
  }

  // Only one at a time: stacking two images looks accidental, not edited.
  const item = active[0];
  const frameStart = Math.round(item.t_start * fps);
  const frameEnd = Math.round(item.t_end * fps);

  const fadeIn = interpolate(frame, [frameStart, frameStart + FADE_FRAMES], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const fadeOut = interpolate(frame, [frameEnd - FADE_FRAMES, frameEnd], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const opacity = Math.min(fadeIn, fadeOut);

  // Entrance: rises 24px while fading in. Subtle, not bouncy.
  const translateY = interpolate(fadeIn, [0, 1], [24, 0]);

  const maxWidth = Math.round(WIDTH * 0.62);
  const maxHeight = Math.round(HEIGHT * 0.26);

  const corner = item.corner ?? "top";
  const position: React.CSSProperties =
    corner === "top_left"
      ? { top: 150, left: 56 }
      : corner === "top_right"
        ? { top: 150, right: 56 }
        : { top: 150, left: 0, right: 0, justifyContent: "center" };

  return (
    <div
      style={{
        position: "absolute",
        display: "flex",
        ...position,
        opacity,
        transform: `translateY(${translateY}px)`,
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          borderRadius: 18,
          overflow: "hidden",
          boxShadow: "0 18px 48px rgba(0,0,0,0.55)",
          border: "3px solid rgba(255,255,255,0.22)",
          backgroundColor: "rgba(0,0,0,0.35)",
          maxWidth,
        }}
      >
        <Img
          src={resolveSrc(item.path)}
          style={{
            display: "block",
            maxWidth,
            maxHeight,
            width: "auto",
            height: "auto",
            objectFit: "contain",
          }}
        />
      </div>
    </div>
  );
};
