import {
  CURSOR_ONLY_ERROR_FRACTION,
  VALIDATION_POINTS,
  type ValidationOutcome,
} from "@/capture/calibrationMath";
import * as style from "@/capture/styles";

/**
 * The measurement, said out loud to the person it was taken from.
 *
 * `calibration_error_px` decides whether a session is `webcam` or
 * `cursor_only`, and it is written into the session document - but it used to
 * be shown to nobody, and the 12%-of-screen-width standard it is judged against
 * (SPEC M2, `CURSOR_ONLY_ERROR_FRACTION`) was never stated to the person being
 * judged. What arrived instead was a bare verdict: a system telling someone it
 * did not trust itself, with no number, and no way for them to tell whether the
 * tracker was working or their face was simply badly lit.
 *
 * So every branch says the same three things: what was measured, what it was
 * compared with, and what follows. The threshold is interpolated from the
 * constant rather than typed into the copy, so a shopper can never be quoted a
 * limit that is not the one actually being applied to them.
 *
 * This is presentation only. Nothing here rounds, clamps or re-derives what the
 * session records - `modeFromValidation` already decided all of it.
 */

export interface CalibrationReportProps {
  outcome: ValidationOutcome;
  /** The same screen width the threshold was applied to, and that `screen_w` records. */
  screenWidthPx: number;
}

/** "5.8", "17.3", "12" - one decimal, and no pointless trailing ".0". */
function percent(value: number): string {
  return value.toFixed(1).replace(/\.0$/, "");
}

const THRESHOLD = `${percent(CURSOR_ONLY_ERROR_FRACTION * 100)}%`;

const FOLLOWS_MOUSE = "eye tracking is off for this session and your mouse stands in for it";

/**
 * One plain-language sentence about the calibration, for a member of the public
 * rather than for whoever wrote the tracker.
 *
 * Three cases, and none of them may invent a number:
 *   - no measurement at all (the camera was refused, or the tracker never
 *     started, so no validation was ever run) - say so, rather than printing a
 *     confident "0 px";
 *   - a measurement and no usable screen width - the exact case
 *     `modeFromValidation` drops to cursor_only with the error still attached -
 *     report the pixels and admit the percentage cannot be worked out;
 *   - a measurement and a screen to compare it with - the normal path, on both
 *     sides of the threshold.
 */
export function calibrationReportText(
  outcome: ValidationOutcome,
  screenWidthPx: number,
): string {
  const error = outcome.calibration_error_px;
  if (error === null) {
    return `No accuracy figure was measured, so there is nothing to compare with the ${THRESHOLD} cut-off - ${FOLLOWS_MOUSE}.`;
  }

  const measured = `Across the ${VALIDATION_POINTS.length} check points, the estimate was off by ${Math.round(error)} pixels on average`;

  if (!Number.isFinite(screenWidthPx) || screenWidthPx <= 0) {
    return `${measured}. Your screen width could not be read, so that cannot be turned into a share of the screen or compared with the ${THRESHOLD} cut-off, and ${FOLLOWS_MOUSE}.`;
  }

  const share = `${percent((error / screenWidthPx) * 100)}% of your screen's width`;
  if (outcome.mode === "webcam") {
    return `${measured} - ${share}. Anything up to ${THRESHOLD} is close enough to use, so eye tracking is on for this session.`;
  }
  return `${measured} - ${share}. The cut-off is ${THRESHOLD}, so ${FOLLOWS_MOUSE}. That is a limit of the webcam, not of you.`;
}

/** The sentence above, on the "you are set" screen, next to the verdict it explains. */
export function CalibrationReport({
  outcome,
  screenWidthPx,
}: CalibrationReportProps): JSX.Element {
  return (
    <p style={style.paragraph} data-testid="done-accuracy">
      {calibrationReportText(outcome, screenWidthPx)}
    </p>
  );
}
