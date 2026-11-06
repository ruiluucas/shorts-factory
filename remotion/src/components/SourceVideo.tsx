import React from "react";
import { OffthreadVideo, Video } from "remotion";
import { toVideoSrc } from "../types";

type Props = {
  src: string;
  muted?: boolean;
  style?: React.CSSProperties;
};

const isRemote = (p: string) => /^https?:\/\//i.test(p);

/**
 * Remotion serves the bundle from the filesystem root of a local static server
 * (`serve-handler` resolves `path.join(bundleDir, urlPath)`), and copies the
 * project `public/` folder into `<bundle>/public/`.
 *
 * Because `window.remotion_staticBase` is null during `renderMedia`, the
 * prefix must be supplied explicitly:
 *
 *   - http(s) URL          -> <OffthreadVideo> (fast, parallel frame pulls)
 *   - "public/<file>"      -> already bundle-relative
 *   - bare "file.mp4"      -> served from `public/`, must be prefixed
 *   - "/abs/path/video.mp4"-> NOT servable by the bundle server; we copy it
 *                             into the bundle's public/ at module load is not
 *                             possible in the browser, so such paths must be
 *                             staged by the renderer (see main.py stage_render,
 *                             which copies the source into remotion/public/).
 *
 * Hence: absolute disk paths are expected to have been staged already under
 * a name passed through props; the pipeline always hands us bundle-relative
 * names.
 */
export const resolveSrc = (raw: string): string => {
  const resolved = toVideoSrc(raw);

  if (isRemote(resolved)) {
    return resolved;
  }
  if (resolved.startsWith("public/") || resolved.startsWith("/public/")) {
    return resolved.startsWith("/") ? resolved : `/${resolved}`;
  }
  if (resolved.startsWith("/")) {
    // Absolute filesystem path: strip to its basename and look in public/.
    const name = resolved.split("/").filter(Boolean).pop() ?? "";
    return `/public/${name}`;
  }
  return `/public/${resolved}`;
};

export const SourceVideo: React.FC<Props> = ({ src, muted, style }) => {
  const resolved = resolveSrc(src);

  if (isRemote(resolved)) {
    return <OffthreadVideo src={resolved} muted={muted} style={style} />;
  }

  return <Video src={resolved} muted={muted} style={style} />;
};
