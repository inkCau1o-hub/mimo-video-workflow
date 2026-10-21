import { useEffect, useReducer } from "react";
import { createDraft, draftDirty, draftReducer } from "./draftHistory.js";

export function useShotDraft(value, revision, scope) {
  const savedKey = JSON.stringify(value);
  const [state, dispatch] = useReducer(draftReducer, null, () => createDraft(value, revision, scope));
  useEffect(() => {
    dispatch({ type: "sync", value: JSON.parse(savedKey), revision, scope });
  }, [savedKey, revision, scope]);
  const dirty = draftDirty(state);
  return {
    ...state, dirty, conflict: dirty && (state.base !== savedKey || (revision !== undefined && state.revision !== undefined && state.revision !== revision)),
    setValue: (next) => dispatch({ type: "edit", value: next }),
    undo: () => dispatch({ type: "undo" }), redo: () => dispatch({ type: "redo" }),
    reset: () => dispatch({ type: "reset", value, revision, scope }),
    saved: (next, nextRevision) => dispatch({ type: "saved", value: next, revision: nextRevision, scope }),
  };
}
