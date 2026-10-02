export const EASE_OUT = [0.215, 0.61, 0.355, 1] as const

export const spring = { type: 'spring', stiffness: 520, damping: 32, mass: 0.7 } as const
export const softSpring = { type: 'spring', stiffness: 320, damping: 30 } as const
export const fade = { duration: 0.2, ease: EASE_OUT } as const
