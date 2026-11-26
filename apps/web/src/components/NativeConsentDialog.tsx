import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";

/**
 * Asks before a SolidWorks or Fusion file is sent to Autodesk for conversion (ADR-018).
 * The wording comes from the server so the text stored with the consent is the text shown here.
 * Cancel is the default focus: nothing leaves the server unless the person confirms.
 */
export function NativeConsentDialog({
  open,
  fileNames,
  consentText,
  usage,
  busy,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  fileNames: string[];
  consentText: string;
  usage: { used: number; cap: number } | null;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const [understood, setUnderstood] = useState(false);

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          setUnderstood(false);
          onCancel();
        }
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="rv-overlay" />
        <Dialog.Content
          className="rv-dialog"
          aria-describedby="native-consent-desc"
          onOpenAutoFocus={(e) => {
            // Focus Cancel, not the confirm button: the safe choice is the default.
            e.preventDefault();
            document.getElementById("native-consent-cancel")?.focus();
          }}
        >
          <Dialog.Title>Send this file to Autodesk?</Dialog.Title>
          <div className="stack" id="native-consent-desc">
            <p>
              <strong>{consentText}</strong>
            </p>
            <ul className="small">
              {fileNames.map((n) => (
                <li key={n}>
                  <span className="file-name">{n}</span>
                </li>
              ))}
            </ul>
            <p className="small">
              Autodesk converts the file in its cloud so we can read it. We delete our copy there
              right after, but the file does leave this server, so do not continue if your
              company does not allow that. You can always export a STEP file yourself instead and
              nothing is sent anywhere.
            </p>
            {usage ? (
              <p className="small muted">
                This server has used {usage.used.toString()} of {usage.cap.toString()} conversions
                this month. A SolidWorks file counts as 2; a Fusion file as 1.
              </p>
            ) : null}
            <label className="check-row">
              <input
                type="checkbox"
                checked={understood}
                onChange={(e) => {
                  setUnderstood(e.target.checked);
                }}
              />
              <span>I understand the file will be sent to Autodesk.</span>
            </label>
            <div className="actions">
              <button
                type="button"
                className="btn btn-primary"
                disabled={!understood || busy}
                onClick={onConfirm}
              >
                {busy ? "Saving…" : "Continue"}
              </button>
              <Dialog.Close id="native-consent-cancel" className="btn btn-secondary">
                Cancel
              </Dialog.Close>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
