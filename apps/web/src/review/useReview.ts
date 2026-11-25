import { useCallback, useEffect, useRef, useState } from "react";
import type { BomDraft, CadNode, JobOut } from "@c2b/ts-types";
import {
  ApiError,
  answerQuestion,
  approveBom,
  editLine,
  getBom,
  getConfig,
  getExcluded,
  getJob,
  listVersions,
  reviewSuggestion,
  type ApprovalResult,
  type VersionSummary,
} from "../lib/api";
import { DEFAULT_BANDS, type Bands } from "./unwrap";

export interface ReviewState {
  job: JobOut | null;
  draft: BomDraft | null;
  versions: VersionSummary[];
  excluded: CadNode[];
  bands: Bands;
  loading: boolean;
  /** True while a change is being saved. */
  busy: boolean;
  /** A problem written for the person (load or save failed). */
  error: string | null;
  /** Counts up after every reload, so details and history panels know to refetch. */
  revision: number;
}

export interface ReviewActions {
  refresh: () => Promise<void>;
  answer: (questionId: string, answer: unknown) => Promise<boolean>;
  decide: (suggestionId: string, decision: "accepted" | "rejected") => Promise<boolean>;
  edit: (lineId: string, field: string, value: unknown) => Promise<boolean>;
  approve: () => Promise<ApprovalResult | null>;
  clearError: () => void;
}

function message(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback;
}

/** Loads the draft and keeps it fresh: every change is saved on the server, then reloaded. */
export function useReview(jobId: string, token: string | null): [ReviewState, ReviewActions] {
  const [state, setState] = useState<ReviewState>({
    job: null,
    draft: null,
    versions: [],
    excluded: [],
    bands: DEFAULT_BANDS,
    loading: true,
    busy: false,
    error: null,
    revision: 0,
  });
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      const [job, draft, versions] = await Promise.all([
        getJob(jobId, token),
        getBom(jobId, token),
        listVersions(jobId, token),
      ]);
      if (!alive.current) return;
      setState((s) => ({ ...s, job, draft, versions, loading: false, revision: s.revision + 1 }));
    } catch (e) {
      if (alive.current) {
        setState((s) => ({
          ...s,
          loading: false,
          error: message(e, "Could not load the BoM. Please try again."),
        }));
      }
    }
  }, [jobId, token]);

  // First load: the draft plus the things that do not change while reviewing.
  useEffect(() => {
    if (!token) return;
    void refresh();
    getConfig()
      .then((c) => {
        if (alive.current && c.confidence_bands) {
          const bands = c.confidence_bands;
          setState((s) => ({ ...s, bands }));
        }
      })
      .catch(() => {
        /* the default bands are used */
      });
    getExcluded(jobId, token)
      .then((excluded) => {
        if (alive.current) setState((s) => ({ ...s, excluded }));
      })
      .catch(() => {
        /* the Excluded tab then says there are none */
      });
  }, [jobId, token, refresh]);

  const save = useCallback(
    async <T>(run: (t: string) => Promise<T>, failure: string): Promise<T | null> => {
      if (!token) return null;
      setState((s) => ({ ...s, busy: true, error: null }));
      try {
        const result = await run(token);
        await refresh();
        return result;
      } catch (e) {
        if (alive.current) setState((s) => ({ ...s, error: message(e, failure) }));
        return null;
      } finally {
        if (alive.current) setState((s) => ({ ...s, busy: false }));
      }
    },
    [token, refresh],
  );

  const actions: ReviewActions = {
    refresh,
    answer: async (questionId, answer) =>
      (await save((t) => answerQuestion(jobId, t, questionId, answer), "Your answer could not be saved.")) !==
      null,
    decide: async (suggestionId, decision) =>
      (await save(
        (t) => reviewSuggestion(jobId, t, suggestionId, decision),
        "Your decision could not be saved.",
      )) !== null,
    edit: async (lineId, field, value) =>
      (await save((t) => editLine(jobId, t, lineId, field, value), "Your edit could not be saved.")) !==
      null,
    approve: () => save((t) => approveBom(jobId, t), "The BoM could not be approved."),
    clearError: () => {
      setState((s) => ({ ...s, error: null }));
    },
  };
  return [state, actions];
}
