import { useEffect, useRef, useState } from "react";
import * as style from "@/capture/styles";

export interface CameraCheckProps {
  onCameraReady: () => void;
  /** A blocked camera is never a dead end: the session carries on cursor-only. */
  onCursorOnly: (reason: string) => void;
}

type State =
  | { status: "idle" }
  | { status: "checking" }
  | { status: "ready" }
  | { status: "failed"; message: string };

/**
 * WebGazer asks for the camera with these constraints; asking for the same ones
 * here means this check tests the camera WebGazer will actually get, and the
 * browser only prompts once.
 */
const CONSTRAINTS: MediaStreamConstraints = { video: { facingMode: "user" } };

function readableFailure(error: unknown): string {
  const name = error instanceof DOMException ? error.name : "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return "The camera was blocked. Your browser may have refused it, or the permission was declined.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "No camera was found on this computer.";
    case "NotReadableError":
      return "The camera is busy - another app or tab is already using it.";
    default:
      return error instanceof Error && error.message.length > 0
        ? `The camera could not be opened: ${error.message}`
        : "The camera could not be opened.";
  }
}

/** Hand the camera straight back: this check only proves that it opens. */
function release(stream: MediaStream): void {
  for (const track of stream.getTracks()) track.stop();
}

export function CameraCheck({ onCameraReady, onCursorOnly }: CameraCheckProps): JSX.Element {
  const [state, setState] = useState<State>({ status: "idle" });
  const live = useRef(true);

  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  async function check(): Promise<void> {
    setState({ status: "checking" });
    const media = navigator.mediaDevices;
    if (media === undefined || typeof media.getUserMedia !== "function") {
      setState({
        status: "failed",
        message:
          "This browser exposes no camera API. Over plain http only localhost is allowed a camera.",
      });
      return;
    }
    try {
      const stream = await media.getUserMedia(CONSTRAINTS);
      // Released whether or not this component is still on screen - WebGazer
      // opens its own stream a moment later, and two streams on one camera is
      // how you get a NotReadableError.
      release(stream);
      if (live.current) setState({ status: "ready" });
    } catch (error) {
      if (live.current) setState({ status: "failed", message: readableFailure(error) });
    }
  }

  return (
    <div style={style.screen}>
      <div style={style.panel}>
        <h1 style={style.heading}>Camera check</h1>
        <p style={style.paragraph}>
          Your browser will ask for permission. Sit where you normally would, with
          your face lit from the front rather than from behind.
        </p>

        {state.status === "checking" && (
          <p style={style.paragraph} data-testid="camera-checking">
            Waiting for the camera...
          </p>
        )}
        {state.status === "ready" && (
          <p style={style.paragraph} data-testid="camera-ready">
            The camera works. It is switched off again until calibration starts.
          </p>
        )}
        {state.status === "failed" && (
          <p style={style.paragraph} data-testid="camera-error">
            {state.message} You can still take part - we will follow your mouse
            instead of your eyes.
          </p>
        )}

        <div style={style.buttonRow}>
          {state.status === "ready" ? (
            <button
              type="button"
              data-testid="camera-continue"
              style={style.primaryButton}
              onClick={onCameraReady}
            >
              Continue to calibration
            </button>
          ) : (
            <button
              type="button"
              data-testid="camera-start"
              disabled={state.status === "checking"}
              style={{
                ...style.primaryButton,
                ...style.disabledButton(state.status === "checking"),
              }}
              onClick={() => void check()}
            >
              {state.status === "failed" ? "Try the camera again" : "Turn the camera on"}
            </button>
          )}
          <button
            type="button"
            data-testid="camera-cursor-only"
            style={style.secondaryButton}
            onClick={() =>
              onCursorOnly(
                state.status === "failed" ? state.message : "The shopper chose not to use the camera.",
              )
            }
          >
            Continue without the camera
          </button>
        </div>
      </div>
    </div>
  );
}
