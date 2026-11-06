/**
 * Shared prop contracts for the GTA Shorts Factory Remotion compositions.
 *
 * These mirror core/schemas.py (WordSegment / EditPlan) so that the JSON
 * produced by main.py can be fed straight in as `--props=edit_plan.json`.
 */

export type WordSegment = {
  word: string;
  start: number;
  end: number;
  score?: number;
  speaker?: string | null;
};

export type SfxTrigger = {
  /** absolute seconds inside the source video */
  time?: number;
  /** alternative key names tolerated from the detector module */
  start?: number;
  end?: number;
  /** asset name, e.g. "boom.wav" */
  name?: string;
  sfx?: string;
  file?: string;
  label?: string;
  /** relative gain in dB */
  gain_db?: number;
  volume?: number;
};

/** The full edit plan as emitted by main.py / build_edit_plan(). */
export type EditPlanProps = {
  job_id?: string;
  composition_template?: string;
  candidate_id?: string;
  source_video_path: string;
  output_duration_seconds?: number;
  crop_mode?: string;
  words?: WordSegment[];
  sfx_triggers?: SfxTrigger[];
  background_music_track?: string | null;
  /** extra passthrough fields are tolerated */
  [key: string]: unknown;
};

/** Normalized, composition-safe view of an edit plan. */
export type NormalizedPlan = {
  jobId: string;
  candidateId: string;
  sourceVideoPath: string;
  durationSeconds: number;
  words: WordSegment[];
  sfx: { time: number; asset: string; volume: number; label?: string }[];
  music: string | null;
  titleText: string;
};

export const FPS = 30;
export const WIDTH = 1080;
export const HEIGHT = 1920;

/** Fallback duration when the plan does not state one. */
export const DEFAULT_DURATION_SECONDS = 15;

/**
 * Remotion's OffthreadVideo resolves a leading `/` against the *bundled*
 * webpack output directory (e.g. `<bundle>/tmp/gsf_test/source.mp4`), not the
 * filesystem root, and cannot fetch `file://` URLs at all. Absolute local
 * paths are therefore kept verbatim and rendered with `<Video>` instead — see
 * components/SourceVideo.tsx.
 */
export const toVideoSrc = (p: string): string => {
  if (!p) {
    return "";
  }
  if (/^file:\/\//i.test(p)) {
    return p.replace(/^file:\/\//i, "");
  }
  return p;
};

export const normalizeVolume = (trigger: SfxTrigger): number => {
  if (typeof trigger.volume === "number") {
    return Math.max(0, Math.min(1, trigger.volume));
  }
  if (typeof trigger.gain_db === "number") {
    // -12 dB -> ~0.25 linear
    return Math.max(0, Math.min(1, Math.pow(10, trigger.gain_db / 20)));
  }
  return 0.8;
};

export const normalizePlan = (plan: EditPlanProps): NormalizedPlan => {
  const durationRaw = plan.output_duration_seconds;
  const durationSeconds =
    typeof durationRaw === "number" && durationRaw > 0
      ? durationRaw
      : DEFAULT_DURATION_SECONDS;

  const words = (plan.words ?? [])
    .filter(
      (w) =>
        w &&
        typeof w.start === "number" &&
        typeof w.end === "number" &&
        w.end >= w.start,
    )
    .slice()
    .sort((a, b) => a.start - b.start);

  const sfx = (plan.sfx_triggers ?? [])
    .map((t) => {
      const time = typeof t.time === "number" ? t.time : (t.start ?? 0);
      const asset = t.name ?? t.sfx ?? t.file ?? "sfx.wav";
      return {
        time,
        asset,
        volume: normalizeVolume(t),
        label: t.label,
      };
    })
    .filter((t) => Number.isFinite(t.time))
    .sort((a, b) => a.time - b.time);

  return {
    jobId: plan.job_id ?? "job",
    candidateId: plan.candidate_id ?? "cand",
    sourceVideoPath: toVideoSrc(plan.source_video_path ?? ""),
    durationSeconds,
    words,
    sfx,
    music: plan.background_music_track ?? null,
    titleText: (plan.job_id ?? "GTA") as string,
  };
};
