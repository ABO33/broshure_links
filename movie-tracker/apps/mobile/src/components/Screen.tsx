import type { PropsWithChildren } from 'react';
import { StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { colors, spacing } from '@/theme';

type ScreenProps = PropsWithChildren<{ padded?: boolean }>;

export function Screen({ children, padded = false }: ScreenProps) {
  return <SafeAreaView style={[styles.screen, padded && styles.padded]}>{children}</SafeAreaView>;
}

const styles = StyleSheet.create({
  screen: { backgroundColor: colors.background, flex: 1 },
  padded: { gap: spacing.xl, padding: spacing.lg },
});
