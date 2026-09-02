import { calculateProgress, computeSectionStatus } from './FormContext';

describe('FormStandard foundation logic', () => {
  describe('calculateProgress', () => {
    test('returns 100% for empty fields', () => {
      const result = calculateProgress({});
      expect(result.percentage).toBe(100);
      expect(result.completed).toBe(0);
      expect(result.total).toBe(0);
    });

    test('returns 0% when all required fields are empty', () => {
      const fields = {
        a: { isRequired: true, value: '' },
        b: { isRequired: true, value: '' },
      };
      const result = calculateProgress(fields);
      expect(result.percentage).toBe(0);
      expect(result.completed).toBe(0);
      expect(result.total).toBe(2);
    });

    test('returns 50% when half of required fields are filled', () => {
      const fields = {
        a: { isRequired: true, value: 'hello' },
        b: { isRequired: true, value: '' },
      };
      const result = calculateProgress(fields);
      expect(result.percentage).toBe(50);
      expect(result.completed).toBe(1);
      expect(result.total).toBe(2);
    });

    test('returns 100% when all required fields are filled', () => {
      const fields = {
        a: { isRequired: true, value: 'hello' },
        b: { isRequired: true, value: 'world' },
      };
      const result = calculateProgress(fields);
      expect(result.percentage).toBe(100);
      expect(result.completed).toBe(2);
      expect(result.total).toBe(2);
    });

    test('ignores optional fields in progress calculation', () => {
      const fields = {
        a: { isRequired: true, value: 'hello' },
        b: { isRequired: false, value: '' },
        c: { isRequired: false, value: '' },
      };
      const result = calculateProgress(fields);
      expect(result.percentage).toBe(100);
      expect(result.completed).toBe(1);
      expect(result.total).toBe(1);
    });

    test('treats null/undefined values as empty', () => {
      const fields = {
        a: { isRequired: true, value: null },
        b: { isRequired: true, value: undefined },
      };
      const result = calculateProgress(fields);
      expect(result.percentage).toBe(0);
    });
  });

  describe('computeSectionStatus', () => {
    test('returns neutral for empty fields', () => {
      expect(computeSectionStatus([])).toBe('neutral');
      expect(computeSectionStatus(null)).toBe('neutral');
    });

    test('returns error when any field has error', () => {
      const fields = [
        { isRequired: true, value: 'x', error: null },
        { isRequired: true, value: '', error: 'Required' },
      ];
      expect(computeSectionStatus(fields)).toBe('error');
    });

    test('returns incomplete when required fields are empty', () => {
      const fields = [
        { isRequired: true, value: 'x' },
        { isRequired: true, value: '' },
      ];
      expect(computeSectionStatus(fields)).toBe('incomplete');
    });

    test('returns complete when all required are filled', () => {
      const fields = [
        { isRequired: true, value: 'hello' },
        { isRequired: true, value: 'world' },
      ];
      expect(computeSectionStatus(fields)).toBe('complete');
    });

    test('returns complete for optional-only fields', () => {
      const fields = [
        { isRequired: false, value: '' },
        { isRequired: false, value: '' },
      ];
      expect(computeSectionStatus(fields)).toBe('complete');
    });

    test('error takes priority over incomplete', () => {
      const fields = [
        { isRequired: true, value: '', error: 'bad' },
      ];
      expect(computeSectionStatus(fields)).toBe('error');
    });
  });
});
