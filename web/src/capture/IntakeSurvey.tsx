import { useState } from "react";
import type { Intake } from "@/capture/archetype";
import * as style from "@/capture/styles";

export interface IntakeSurveyProps {
  onSubmit: (intake: Intake) => void;
}

type Answers = Partial<Record<keyof Intake, boolean>>;

/** SPEC 4.3: exactly three questions, and they are the three the archetype uses. */
const QUESTIONS: { key: keyof Intake; question: string }[] = [
  {
    key: "has_list",
    question: "Did you come here meaning to buy something specific - a list in your head or on your phone?",
  },
  {
    key: "same_brand",
    question: "In snacks and drinks, do you usually buy the same brand you always buy?",
  },
  { key: "hurry", question: "Are you in a hurry right now?" },
];

export function IntakeSurvey({ onSubmit }: IntakeSurveyProps): JSX.Element {
  const [answers, setAnswers] = useState<Answers>({});

  const answered = QUESTIONS.every(({ key }) => typeof answers[key] === "boolean");

  function answer(key: keyof Intake, value: boolean): void {
    setAnswers((previous) => ({ ...previous, [key]: value }));
  }

  function submit(): void {
    // The guard is not decoration: an unanswered question must never be
    // silently recorded as "no", it would relabel the shopper's archetype.
    if (!answered) return;
    onSubmit({
      has_list: answers.has_list === true,
      same_brand: answers.same_brand === true,
      hurry: answers.hurry === true,
    });
  }

  return (
    <div style={style.screen}>
      <div style={style.panel}>
        <h1 style={style.heading}>Three quick questions</h1>
        <p style={style.paragraph}>
          They take ten seconds and they are the only thing we ask about you.
        </p>
        {QUESTIONS.map(({ key, question }) => (
          <div key={key} style={{ margin: "18px 0" }}>
            <div style={{ marginBottom: 8 }}>{question}</div>
            <div style={{ display: "flex", gap: 10 }}>
              <button
                type="button"
                data-testid={`intake-${key}-yes`}
                style={style.choiceButton(answers[key] === true)}
                onClick={() => answer(key, true)}
              >
                Yes
              </button>
              <button
                type="button"
                data-testid={`intake-${key}-no`}
                style={style.choiceButton(answers[key] === false)}
                onClick={() => answer(key, false)}
              >
                No
              </button>
            </div>
          </div>
        ))}
        <div style={style.buttonRow}>
          <button
            type="button"
            data-testid="intake-continue"
            disabled={!answered}
            style={{ ...style.primaryButton, ...style.disabledButton(!answered) }}
            onClick={submit}
          >
            Continue
          </button>
        </div>
      </div>
    </div>
  );
}
