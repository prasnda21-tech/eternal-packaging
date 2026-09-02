import { useMemo } from 'react';

/**
 * Lightweight hook for existing pages to compute form completion progress
 * without requiring FormStandardProvider.
 *
 * Usage:
 *   const progress = useFormProgress([
 *     { id: 'name', value: name, isRequired: true },
 *     { id: 'email', value: email, isRequired: true },
 *     { id: 'phone', value: phone, isRequired: false },
 *   ]);
 *
 * Returns: { percentage, completed, total }
 */
export function useFormProgress(fields = []) {
  return useMemo(() => {
    const required = fields.filter((f) => f.isRequired);
    if (required.length === 0) return { percentage: 100, completed: 0, total: 0 };

    const filled = required.filter((f) => {
      const v = f.value;
      if (v === undefined || v === null || v === '') return false;
      if (Array.isArray(v) && v.length === 0) return false;
      return true;
    });

    return {
      percentage: Math.round((filled.length / required.length) * 100),
      completed: filled.length,
      total: required.length,
    };
  }, [fields]);
}

/**
 * Compute section status from an array of field descriptors.
 * Each field: { value, isRequired, error }
 *
 * Returns: 'complete' | 'incomplete' | 'error' | 'neutral'
 */
export function computeStatus(fields = []) {
  if (!fields || fields.length === 0) return 'neutral';
  if (fields.some((f) => !!f.error)) return 'error';

  const required = fields.filter((f) => f.isRequired);
  if (required.length === 0) return 'complete';

  const filled = required.filter((f) => {
    const v = f.value;
    return v !== undefined && v !== null && v !== '' && !(Array.isArray(v) && v.length === 0);
  });

  return filled.length >= required.length ? 'complete' : 'incomplete';
}
