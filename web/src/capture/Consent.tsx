import * as style from "@/capture/styles";

export interface ConsentProps {
  onAgree: () => void;
  onDecline: () => void;
}

/**
 * Consent is a decision the shopper makes here, on this screen. It is never
 * defaulted, never pre-ticked, and declining is a real option that ends the
 * flow without a session and without ever asking for the camera.
 */
export function Consent({ onAgree, onDecline }: ConsentProps): JSX.Element {
  return (
    <div style={style.screen}>
      <div style={style.panel}>
        <h1 style={style.heading}>Before we start</h1>
        <p style={style.paragraph}>
          We are studying where people look on a supermarket shelf. If you agree,
          your webcam is used to estimate where on the screen you are looking
          while you shop.
        </p>
        <ul style={style.list}>
          <li>
            The camera image is processed <strong>in your browser</strong>. No
            video, image or frame is stored, uploaded or shown to anyone.
          </li>
          <li>
            Four numbers per estimate leave this computer: screen x, screen y, a
            confidence and a timestamp. Nothing else.
          </li>
          <li>
            You will not see a dot following your eyes - watching it would change
            where you look.
          </li>
          <li>The camera is released as soon as the setup is finished.</li>
          <li>
            The session is anonymous: no name, no email, no account. If the
            camera does not work we simply follow your mouse instead.
          </li>
        </ul>
        <div style={style.buttonRow}>
          <button
            type="button"
            data-testid="consent-agree"
            style={style.primaryButton}
            onClick={onAgree}
          >
            I agree - start the setup
          </button>
          <button
            type="button"
            data-testid="consent-decline"
            style={style.secondaryButton}
            onClick={onDecline}
          >
            No thanks
          </button>
        </div>
        <p style={style.note}>
          Nothing has started yet. The camera is only requested after you agree.
        </p>
      </div>
    </div>
  );
}

/** The end of the road for a shopper who declined: no camera, no session. */
export function ConsentDeclined(): JSX.Element {
  return (
    <div style={style.screen}>
      <div style={style.panel} data-testid="consent-declined">
        <h1 style={style.heading}>That is completely fine</h1>
        <p style={style.paragraph}>
          No session was started, the camera was never opened and nothing was
          recorded. Thank you for your time - you can close this tab.
        </p>
      </div>
    </div>
  );
}
