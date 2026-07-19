# Physics model checklist

Use this reference for charged particles, electric fields, magnetic fields, circular boundaries, and repeated region crossings.

## Sign and direction

1. Declare a coordinate system and the positive out-of-plane direction.
2. Compute the positive-charge direction from

   $$
   \vec F=q\vec v\times\vec B.
   $$

3. Reverse the force only when $q<0$.
4. Confirm that the magnetic force points toward the simulated circle center.
5. Check the same charge sign independently with electric work:

   $$
   \Delta K=q(\varphi_{\text{start}}-\varphi_{\text{end}}).
   $$

If a particle moves outward through an outward electric field while slowing down, it must be negatively charged.

## Magnetic circular motion

Use

$$
r=\frac{mv}{|q|B}.
$$

Check the following separately:

- geometric radius from the boundary chord/angle;
- dynamic radius from $mv/(|q|B)$;
- rotation direction from the sign of $q$;
- tangent continuity at every boundary;
- whether the physical arc is the minor or major arc between intersections.

Do not draw a visually convenient short arc if the velocity direction requires the long arc outside the boundary.

## Radial electric region

- Draw $\vec E$ in its stated radial direction.
- Draw $\vec F_E=q\vec E$, which reverses for a negative charge.
- Label outward motion as acceleration or deceleration only after checking $\vec F_E\cdot\vec v$.
- Do not invent a linear speed law if the field magnitude as a function of radius is unspecified. A qualitative transition label is safer.

## Repeated crossings

Maintain angles and events separately. For a repeated two-field pattern, a table such as this prevents missed cases:

| stage | event | accumulated boundary angle |
|---|---|---|
| 0 | initial point | $0$ |
| 1 | first inner magnetic exit | $-\alpha$ |
| 2 | first outer magnetic exit | $-\alpha+\delta$ |
| 3 | second inner magnetic exit | $-2\alpha+\delta$ |
| 4 | second outer magnetic exit | $-2\alpha+2\delta$ |
| 5 | third inner magnetic exit | $-3\alpha+2\delta$ |

Test every inner-boundary arrival and departure for equality with the target point modulo $2\pi$. Exclude the initial point unless the wording explicitly counts it.

“Before the third entry” includes a crossing before the second entry. “After the second entry and before the third entry” does not.

## Independent verification

Use at least two applicable checks:

- force direction at a named point;
- energy/work sign;
- dimensions;
- substitution into geometry;
- tangent continuity;
- endpoint coordinates;
- limiting behavior as $B\to0$ or $B\to\infty$;
- a second algebraic or geometric derivation.

The simulator and written solution must use the same sign convention, case list, and event wording.
