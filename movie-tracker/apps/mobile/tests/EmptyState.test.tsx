import { render, screen } from '@testing-library/react-native';

import { EmptyState } from '@/components/EmptyState';

describe('EmptyState', () => {
  it('renders its accessible message', () => {
    render(<EmptyState message="Nothing saved yet" />);

    expect(screen.getByText('Nothing saved yet')).toBeOnTheScreen();
  });
});
