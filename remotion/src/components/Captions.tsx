import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import type { WordSegment } from "../types";

type Props = {
  words: WordSegment[];
  /** vertical offset from the bottom of the frame, px */
  bottom?: number;
  fontSize?: number;
  maxWordsPerCard?: number;
  accentColor?: string;
  /** optional second speaker color (diarization) */
  speakerColors?: Record<string, string>;
};

const DEFAULT_SPEAKER_COLORS: Record<string, string> = {
  SPEAKER_00: "#FFFFFF",
  SPEAKER_01: "#7CF0FF",
  SPEAKER_02: "#FFD866",
  SPEAKER_03: "#FF7CB0",
};

/** Split words into caption cards of at most N words each. */
const buildCards = (words: WordSegment[], maxWords: number) => {
  const cards: WordSegment[][] = [];
  let current: WordSegment[] = [];
  for (const w of words) {
    if (current.length >= maxWords) {
      cards.push(current);
      current = [];
    }
    current.push(w);
  }
  if (current.length > 0) {
    cards.push(current);
  }
  return cards;
};

/**
 * Word-level "karaoke" captions. The active word is highlighted with the
 * accent color and scaled up; already-spoken words are dimmed. Timing is
 * driven purely by frame/fps against each word's start/end seconds.
 */
export const Captions: React.FC<Props> = ({
  words,
  bottom = 260,
  fontSize = 64,
  maxWordsPerCard = 4,
  accentColor = "#FFE600",
  speakerColors = DEFAULT_SPEAKER_COLORS,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;

  if (!words || words.length === 0) {
    return null;
  }

  const cards = buildCards(words, maxWordsPerCard);
  const activeCard = cards.find((card) => {
    const start = card[0].start;
    const end = card[card.length - 1].end;
    return t >= start - 0.08 && t <= end + 0.12;
  });

  if (!activeCard) {
    return null;
  }

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems: "center",
        paddingBottom: bottom,
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "0 18px",
          justifyContent: "center",
          alignItems: "flex-end",
          maxWidth: 940,
          padding: "18px 34px",
          borderRadius: 18,
          backgroundColor: "rgba(0,0,0,0.62)",
          border: "2px solid rgba(255,255,255,0.12)",
        }}
      >
        {activeCard.map((w, i) => {
          const isActive = t >= w.start && t <= w.end;
          const isSpoken = t > w.end;
          const speakerColor =
            (w.speaker && speakerColors[w.speaker]) || "#FFFFFF";

          const progress =
            isActive && w.end > w.start
              ? Math.min(1, Math.max(0, (t - w.start) / (w.end - w.start)))
              : 0;

          // subtle pop-in when the word becomes active
          const scale = isActive ? 1 + 0.06 * (1 - progress) : 1;

          return (
            <span
              key={`${w.word}-${w.start}-${i}`}
              style={{
                fontFamily:
                  "'Arial Black', 'Montserrat ExtraBold', system-ui, sans-serif",
                fontWeight: 900,
                fontSize,
                lineHeight: 1.12,
                letterSpacing: 0.5,
                textTransform: "uppercase",
                color: isActive ? accentColor : isSpoken ? speakerColor : "#F2F2F2",
                opacity: isActive ? 1 : isSpoken ? 0.55 : 0.9,
                transform: `scale(${scale})`,
                textShadow: isActive
                  ? "0 0 24px rgba(255,230,0,0.55), 3px 3px 0 #000"
                  : "3px 3px 0 #000",
                display: "inline-block",
                whiteSpace: "nowrap",
              }}
            >
              {w.word}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
