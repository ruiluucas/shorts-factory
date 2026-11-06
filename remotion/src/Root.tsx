import React from "react";
import { Composition, CalculateMetadataFunction } from "remotion";
import { GTASplitScreen, GTASplitScreenProps } from "./compositions/GTASplitScreen";
import { GTACinematic, GTACinematicProps } from "./compositions/GTACinematic";
import { DEFAULT_DURATION_SECONDS, FPS, HEIGHT, WIDTH } from "./types";

/**
 * Dynamic duration: the composition length is derived from
 * `output_duration_seconds` in the incoming edit plan (inputProps).
 *
 * `npx remotion compositions` renders without props, so a safe default is
 * kept to make the composition discoverable.
 */
const defaultInFrames = Math.round(DEFAULT_DURATION_SECONDS * FPS);

const calculateMetadata: CalculateMetadataFunction<Record<string, unknown>> = ({
  props,
}) => {
  const raw = (props as { output_duration_seconds?: unknown })
    ?.output_duration_seconds;

  const seconds =
    typeof raw === "number" && Number.isFinite(raw) && raw > 0
      ? raw
      : DEFAULT_DURATION_SECONDS;

  // hard clamp so a malformed plan can never produce an absurd render
  const clamped = Math.min(Math.max(seconds, 1), 180);

  return {
    durationInFrames: Math.max(1, Math.round(clamped * FPS)),
    fps: FPS,
    width: WIDTH,
    height: HEIGHT,
  };
};

/** Sample props so Studio and a bare `render` are both usable. */
const sampleProps = {
  job_id: "sample_job",
  candidate_id: "test",
  composition_template: "GTACinematic",
  source_video_path: "https://remotion.media/video.mp4",
  output_duration_seconds: 5,
  words: [
    { word: "OLHA", start: 0.2, end: 0.6, score: 0.9 },
    { word: "ISSO", start: 0.6, end: 1.0, score: 0.9 },
    { word: "QUE", start: 1.0, end: 1.25, score: 0.9 },
    { word: "CAOS", start: 1.25, end: 1.8, score: 0.9 },
  ],
  sfx_triggers: [{ time: 1.5, name: "boom.wav", label: "BOOM" }],
  background_music_track: null,
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="GTASplitScreen"
        component={GTASplitScreen as React.FC<Record<string, unknown>>}
        durationInFrames={defaultInFrames}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={sampleProps as unknown as Record<string, unknown>}
        calculateMetadata={
          calculateMetadata as CalculateMetadataFunction<
            Record<string, unknown>
          >
        }
      />
      <Composition
        id="GTACinematic"
        component={GTACinematic as React.FC<Record<string, unknown>>}
        durationInFrames={defaultInFrames}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={sampleProps as unknown as Record<string, unknown>}
        calculateMetadata={
          calculateMetadata as CalculateMetadataFunction<
            Record<string, unknown>
          >
        }
      />
    </>
  );
};
