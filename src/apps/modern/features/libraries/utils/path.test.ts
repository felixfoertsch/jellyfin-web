import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as userSettings from 'scripts/settings/userSettings';
import { LibraryTab } from 'types/libraryTab';

import { getDefaultViewIndex } from './path';

vi.mock('scripts/settings/userSettings', () => ({ get: vi.fn() }));

beforeEach(() => {
    vi.mocked(userSettings.get).mockReset();
});

describe('getDefaultViewIndex()', () => {
    it('uses cached account defaults independently when switching libraries without local defaults', () => {
        const preferences: Record<string, string> = {
            'landing-youtube': LibraryTab.Episodes,
            'landing-shows': LibraryTab.Series
        };
        vi.mocked(userSettings.get).mockImplementation((key: string, enableOnServer?: boolean) => (
            enableOnServer === false ? null : preferences[key]
        ));

        expect(getDefaultViewIndex('/tv', 'youtube')).toBe(5);
        expect(getDefaultViewIndex('/tv', 'shows')).toBe(0);
        expect(getDefaultViewIndex('/tv', 'youtube')).toBe(5);
    });

    it('preserves the Live TV default screen', () => {
        vi.mocked(userSettings.get).mockReturnValue(LibraryTab.Guide);

        expect(getDefaultViewIndex('/livetv', 'livetv')).toBe(1);
        expect(userSettings.get).toHaveBeenCalledWith('landing-livetv');
    });

    it.each([undefined, null, '', 'unknown', LibraryTab.Movies])('falls back for unsupported TV defaults: %s', value => {
        vi.mocked(userSettings.get).mockReturnValue(value ?? null);

        expect(getDefaultViewIndex('/tv', 'youtube')).toBe(0);
    });

    it.each([undefined, null, ''])('does not read preferences without a library: %s', libraryId => {
        expect(getDefaultViewIndex('/tv', libraryId)).toBe(0);
        expect(userSettings.get).not.toHaveBeenCalled();
    });
});
