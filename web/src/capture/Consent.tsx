import * as style from "@/capture/styles";

export interface ConsentProps {
  onAgree: () => void;
  onDecline: () => void;
}

/**
 * Consent is a decision the shopper makes here, on this screen. It is never
 * defaulted, never pre-ticked, and declining is a real option that ends the
 * flow without a session and without ever asking for the camera.
 *
 * Every sentence below is a claim about what the code does, so it is only
 * allowed to say what the code does. The camera bullet used to read "The camera
 * is released as soon as the setup is finished", which stopped being true the
 * day the tracker handover landed: `CaptureFlow` hands the *running* tracker to
 * the store (`CaptureResult.tracker`) precisely so the camera survives the end
 * of setup, `PlanogramScene` owns it from then on, and it goes back at checkout
 * or when the store unmounts. Only a `cursor_only` verdict gets the camera back
 * at the end of setup. Telling a participant a camera stops earlier than it
 * does is the one bug on this screen that is not a copy bug, so
 * `web/tests/consentCopy.test.tsx` holds the wording against the behaviour.
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
            While you are shopping you will not see a dot following your eyes -
            watching it would change where you look. Before that, during the
            setup, you can ask to see the tracker working for a few seconds.
          </li>
          <li>
            If the setup succeeds, the camera <strong>stays on for as long as
            you are shopping</strong> - that is what produces the estimates. It
            is switched off the moment you check out, or if you close this tab
            or leave this page. If the setup does not reach the accuracy we need,
            the camera is switched off there and then and we follow your mouse
            for the rest of the session.
          </li>
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
