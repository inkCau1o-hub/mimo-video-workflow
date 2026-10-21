const key = (value) => JSON.stringify(value);
export const draftDirty = (state) => key(state.value) !== state.base;
export function createDraft(value, revision, scope) {
  return { value, base: key(value), revision, scope, past: [], future: [] };
}

export function draftReducer(state, action) {
  switch (action.type) {
    case "sync":
      if (state.scope !== action.scope || !draftDirty(state)) return createDraft(action.value, action.revision, action.scope);
      // The first history read may finish after typing has started.
      if (state.revision === undefined && state.base === key(action.value)) return { ...state, revision: action.revision };
      return state;
    case "edit": {
      const value = typeof action.value === "function" ? action.value(state.value) : action.value;
      if (key(value) === key(state.value)) return state;
      return { ...state, value, past: [...state.past, state.value].slice(-100), future: [] };
    }
    case "undo":
      return state.past.length ? { ...state, value: state.past.at(-1), past: state.past.slice(0, -1), future: [state.value, ...state.future] } : state;
    case "redo":
      return state.future.length ? { ...state, value: state.future[0], past: [...state.past, state.value], future: state.future.slice(1) } : state;
    case "saved":
    case "reset":
      return action.scope === state.scope ? createDraft(action.value, action.revision, action.scope) : state;
    default: return state;
  }
}
