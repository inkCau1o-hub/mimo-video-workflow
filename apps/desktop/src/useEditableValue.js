import { useEffect, useState } from "react";

export function useEditableValue(savedValue) {
  const savedKey = JSON.stringify(savedValue);
  const [draft, setDraft] = useState(() => ({ value: savedValue, base: savedKey }));
  const dirty = JSON.stringify(draft.value) !== draft.base;
  useEffect(() => {
    setDraft((previous) => JSON.stringify(previous.value) === previous.base
      ? { value: JSON.parse(savedKey), base: savedKey } : previous);
  }, [savedKey, dirty]);
  return {
    value: draft.value, dirty, conflict: dirty && draft.base !== savedKey,
    setValue: (value) => setDraft((previous) => ({ ...previous, value })),
    reset: () => setDraft({ value: savedValue, base: savedKey }),
    saved: (value) => setDraft({ value, base: JSON.stringify(value) }),
  };
}
