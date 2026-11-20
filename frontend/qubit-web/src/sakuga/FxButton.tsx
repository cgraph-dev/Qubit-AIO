import type { ComponentProps } from "react";
import type { FxCue } from "./ui";
import { playLocalFx } from "./localFx";

/** Native button for fitted panels. Keeps the original Sakuga press cue. */
export function FxButton({ fx = "impact", onClick, ...props }: ComponentProps<"button"> & { fx?: FxCue }) {
  return (
    <button {...props} data-sakuga-native="" onClick={(event) => {
      playLocalFx(fx, event.currentTarget);
      onClick?.(event);
    }} />
  );
}
