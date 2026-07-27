+++
title = "Tamal: The Loader's Three Lives"
date = 2026-07-27T09:00:00
draft = false
description = "Reading Tamal's impure loader end to end: the streaming realization of the pure Tamal.Wire model and the first block in the series that owns a clock, where the series' pure s -> i -> (s, o) steps finally get the mealy that clocks them; how the module is the confluence where the COBS codec, the CRC-8 fold, the UART's four wires, the two BRAMs, and the engine's start pulse and halt level all plug in at once; how the lifecycle FSM lives three lives in a loop --- RxControl listening for control frames, Run standing aside while the engine executes, and Drain sweeping the trace ring back out as one frame --- dispatched by a single case on lPhase; how the daunting eighteen-field state record is really four small records sharing one constructor; how the RX path peels a frame layer by layer and uses a one-byte holdback to separate the trailing CRC from payload while streaming, writing each little-endian word straight through to the instruction store and committing nothing until a separate TRIGGER; how the seven-phase drain dances with the block RAM's one-cycle read latency, unpacks each word into four little-endian bytes, folds a drain CRC, flushes the COBS encoder, and self-paces every byte off txReady so a link with no flow control never overruns; and how the whole streaming machine is welded byte-for-byte to the pure encodeControl/encodeResult oracle by Signal-level property tests."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "loader", "fsm"]
+++

[Yesterday][cobs] we read two pure step functions and made the same
promise about each of them, the promise we have been making all series:
the codec describes *what one byte does to the state*, and someone else
owns the register that makes it sequential. Someone else holds the
clock. We made that promise for the [CRC][crc]'s `step` a month of posts
ago, and again for the [transmitter][tx] and the [receiver][rx], and one
last time yesterday for `cobsDecodeStep` and `cobsEncodeStep`. Five pure
`s -> i -> (s, o)` machines, each admired precisely for *not* having a
clock, each handing the clock off to a someone we kept naming and never
met.

Today we meet the someone. The loader is where the clock finally lives.

It is also where everything else we have built finally meets. The
[receiver][rx] and [transmitter][tx] gave us a byte pipe; the [top][top]
sealed it and left four wires hanging at the loader's door. Behind that
door is a block that does not compute a residue or drive a line or
recover a bit --- it *conducts*. It embeds the [COBS][cobs] codec and
threads it through its own clock; it folds the [CRC][crc] over every byte
in both directions; it drives the two block RAMs on the ports the engine
leaves free; and it pulses the engine awake and waits for it to halt. The
whole series has been building leaves. The loader is the first branch.

Yesterday's post promised this one in as many words: the loader "wears
the silhouette we have now read five times --- a sum for its phase, a
record for the rest, a step function beside a `mealy`." That is exactly
right, and exactly the shape of what follows --- only scaled up. The sum
is now *two* sums. The record is *eighteen* fields. And the step is the
longest in the series. Same silhouette, far more mass. The mass is worth
it, because it buys the loader three distinct **lives** --- listen, run,
drain --- lived over and over in a loop, and a rig you can load, trigger,
read, and load again.

<!-- more -->

[Tamal]: https://github.com/felipebalbi/tamal
[Haskell]: https://www.haskell.org/
[Clash]: https://clash-lang.org
[primer]: https://balbi.sh/posts/tamal-haskell-primer/
[crc]: https://balbi.sh/posts/tamal-crc/
[baudgen]: https://balbi.sh/posts/tamal-uart-baudgen/
[tx]: https://balbi.sh/posts/tamal-uart-tx/
[rx]: https://balbi.sh/posts/tamal-uart-rx/
[top]: https://balbi.sh/posts/tamal-uart-top/
[cobs]: https://balbi.sh/posts/tamal-loader-cobs/
[intro]: https://balbi.sh/posts/tamal-introducing/
[hedgehog]: https://hedgehog.qa

## The entire source

Minus the SPDX header and the doc-comments --- but this time *keeping* the
inline notes on the state fields, which earn their place --- here is
`src/Tamal/Loader.hs`, the longest module in the series:

```haskell
module Tamal.Loader
  ( LoaderIn (..)
  , LoaderOut (..)
  , loader
  ) where

import Clash.Prelude
import Data.Maybe (fromMaybe, isJust)

import Tamal.Crc (crc8Update)
import Tamal.Loader.Cobs
import Tamal.Params (AW, RW)

data LoaderIn = LoaderIn
  { rxByte :: Maybe (BitVector 8)
  , txReady :: Bool
  , halted :: Bool
  , ringPtrIn :: Unsigned RW
  , ringData :: BitVector 32
  }
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)

data LoaderOut = LoaderOut
  { txByte :: Maybe (BitVector 8)
  , instrWr :: Maybe (Unsigned AW, BitVector 32)
  , ringAddr :: Unsigned RW
  , startOut :: Bool
  }
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)

data Lifecycle = RxControl | Run | Drain
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)

data DrainPhase = DrOpcode | DrFetch | DrLatch | DrWordByte | DrCrcByte | DrDrainOut | DrDelim
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)

data LoaderSt = LoaderSt
  { lPhase :: Lifecycle
  , lDec :: DecSt
  , lEnc :: EncSt
  , lHeld :: Maybe (BitVector 8) -- one-byte holdback (separates the trailing CRC)
  , lCrcRx :: BitVector 8 -- running CRC over confirmed bytes
  , lHaveOp :: Bool -- opcode confirmed yet?
  , lOpcode :: BitVector 8
  , lByteIx :: Unsigned 2 -- payload byte within the current word (0..3)
  , lWordAcc :: BitVector 32 -- LE word being assembled
  , lHadPay :: Bool -- any payload byte seen (TRIGGER must have none)
  , lAddr :: Unsigned AW -- next instr write slot
  , lFull :: Bool -- instr store overflowed (> 2^AW words)
  , lDrn :: DrainPhase
  , lWord :: BitVector 32 -- ring word being emitted
  , lWIx :: Unsigned 2 -- LE byte of lWord (0..3)
  , lCrcTx :: BitVector 8 -- running CRC over the drain
  , lDrCnt :: Unsigned RW -- ring record index being fetched
  , lTerm :: Bool -- fetching/emitting the terminator word
  }
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)

initLoader :: LoaderSt
initLoader =
  LoaderSt -- RxControl, both codecs seeded, every counter 0, every flag False
    { lPhase = RxControl
    , lDec = initDec
    , lEnc = initEnc
    , {- … the remaining fifteen fields, all at their zero/False resting value … -}
    }

idleOut :: LoaderOut
idleOut = LoaderOut{txByte = Nothing, instrWr = Nothing, ringAddr = 0, startOut = False}

loader :: (HiddenClockResetEnable dom) => Signal dom LoaderIn -> Signal dom LoaderOut
loader = mealy loaderStep initLoader

loaderStep :: LoaderSt -> LoaderIn -> (LoaderSt, LoaderOut)
loaderStep s inp = case lPhase s of
  RxControl -> rxStep s inp
  Run -> runStep s inp
  Drain -> drainStep s inp

runStep :: LoaderSt -> LoaderIn -> (LoaderSt, LoaderOut)
runStep s inp
  | halted inp =
      ( s{lPhase = Drain, lEnc = initEnc, lDrn = DrOpcode, lCrcTx = 0, lDrCnt = 0, lWIx = 0, lTerm = False}
      , idleOut
      )
  | otherwise = (s, idleOut)

rxStep :: LoaderSt -> LoaderIn -> (LoaderSt, LoaderOut)
rxStep s inp =
  let din = case rxByte inp of
        Just 0 -> (Nothing, True) -- delimiter => frame end
        Just b -> (Just b, False)
        Nothing -> (Nothing, False)
      (dec', (mDec, done, bad)) = cobsDecodeStep (lDec s) din
      s1 = s{lDec = dec'}
   in if done
        then finalize s1 bad
        else case mDec of
          Nothing -> (s1, idleOut)
          Just d ->
            let (s2, mw) = case lHeld s1 of
                  Just h -> confirm s1 h
                  Nothing -> (s1, Nothing)
             in (s2{lHeld = Just d}, idleOut{instrWr = mw})

confirm :: LoaderSt -> BitVector 8 -> (LoaderSt, Maybe (Unsigned AW, BitVector 32))
confirm s h
  | not (lHaveOp s) =
      ( s
          { lHaveOp = True
          , lOpcode = h
          , lCrcRx = crc8Update (lCrcRx s) h
          , lAddr = if h == 0x01 then 0 else lAddr s
          , lFull = if h == 0x01 then False else lFull s
          }
      , Nothing
      )
  | otherwise =
      let crc' = crc8Update (lCrcRx s) h
          acc' = lWordAcc s .|. (zeroExtend h `shiftL` (8 * fromIntegral (lByteIx s)))
          isLoad = lOpcode s == 0x01
       in if lByteIx s == 3
            then
              let doWrite = isLoad && not (lFull s)
                  (addr', full') = if lAddr s == maxBound then (lAddr s, True) else (lAddr s + 1, lFull s)
               in ( s
                      { lCrcRx = crc'
                      , lWordAcc = 0
                      , lByteIx = 0
                      , lHadPay = True
                      , lAddr = if isLoad then addr' else lAddr s
                      , lFull = if isLoad then full' else lFull s
                      }
                  , if doWrite then Just (lAddr s, acc') else Nothing
                  )
            else
              ( s{lCrcRx = crc', lWordAcc = acc', lByteIx = lByteIx s + 1, lHadPay = True}
              , Nothing
              )

finalize :: LoaderSt -> Bool -> (LoaderSt, LoaderOut)
finalize s bad =
  let crcCand = fromMaybe 0 (lHeld s)
      crcGood = not bad && isJust (lHeld s) && lHaveOp s && lCrcRx s == crcCand
      trigOk = crcGood && lOpcode s == 0x02 && not (lHadPay s)
      s0 = resetFrame s
   in if trigOk
        then (s0{lPhase = Run}, idleOut{startOut = True})
        else (s0, idleOut)

resetFrame :: LoaderSt -> LoaderSt
resetFrame s =
  s
    { lDec = initDec
    , lHeld = Nothing
    , lCrcRx = 0
    , lHaveOp = False
    , lOpcode = 0
    , lByteIx = 0
    , lWordAcc = 0
    , lHadPay = False
    }

drainStep :: LoaderSt -> LoaderIn -> (LoaderSt, LoaderOut)
drainStep s inp = case lDrn s of
  DrOpcode ->
    feedByte s inp 0x81 False (\s' -> s'{lDrn = DrFetch, lDrCnt = 0, lTerm = False})
  DrWordByte ->
    feedByte s inp (leByte (lWord s) (lWIx s)) False (afterWordByte inp)
  DrCrcByte ->
    feedByte s inp (lCrcTx s) True (\s' -> s'{lDrn = DrDrainOut})
  DrFetch ->
    let addr = if lTerm s then maxBound else lDrCnt s
        (enc', (_, mOut, _)) = cobsEncodeStep (lEnc s) (Nothing, txReady inp)
     in (s{lEnc = enc', lDrn = DrLatch}, idleOut{txByte = mOut, ringAddr = addr})
  DrLatch ->
    let addr = if lTerm s then maxBound else lDrCnt s
        (enc', (_, mOut, _)) = cobsEncodeStep (lEnc s) (Nothing, txReady inp)
     in ( s{lEnc = enc', lWord = ringData inp, lWIx = 0, lDrn = DrWordByte}
        , idleOut{txByte = mOut, ringAddr = addr}
        )
  DrDrainOut ->
    let (enc', (_, mOut, encDone)) = cobsEncodeStep (lEnc s) (Nothing, txReady inp)
     in (s{lEnc = enc', lDrn = if encDone then DrDelim else DrDrainOut}, idleOut{txByte = mOut})
  DrDelim ->
    if txReady inp
      then (resetFrame s{lPhase = RxControl}, idleOut{txByte = Just 0})
      else (s, idleOut{txByte = Nothing})
 where
  afterWordByte i s'
    | lWIx s' /= 3 = s'{lWIx = lWIx s' + 1}
    | lTerm s' = s'{lDrn = DrCrcByte}
    | lDrCnt s' + 1 >= ringPtrIn i = s'{lTerm = True, lDrn = DrFetch}
    | otherwise = s'{lDrCnt = lDrCnt s' + 1, lDrn = DrFetch}

feedByte ::
  LoaderSt -> LoaderIn -> BitVector 8 -> Bool -> (LoaderSt -> LoaderSt) -> (LoaderSt, LoaderOut)
feedByte s inp b lst advance =
  let (enc', (readyIn, mOut, _)) = cobsEncodeStep (lEnc s) (Just (b, lst), txReady inp)
      s1 = s{lEnc = enc'}
      s2 =
        if readyIn
          then advance s1{lCrcTx = if lst then lCrcTx s1 else crc8Update (lCrcTx s1) b}
          else s1
   in (s2, idleOut{txByte = mOut})

leByte :: BitVector 32 -> Unsigned 2 -> BitVector 8
leByte w i = case i of
  0 -> slice d7 d0 w
  1 -> slice d15 d8 w
  2 -> slice d23 d16 w
  _ -> slice d31 d24 w
```

A dozen things, top to bottom: a three-name export list; the two I/O
records `LoaderIn` and `LoaderOut`; two little sums, `Lifecycle` and
`DrainPhase`; the eighteen-field `LoaderSt` that is the machine's whole
memory; two initialisers; the `mealy` one-liner and the `loaderStep` that
dispatches on phase; and then the three step functions with their helpers.
We wave past the export list and imports, slow right down for the types,
and walk the machine one life at a time.

## The ritual, skipped

You know this opening beat cold. A module header with its export list; the
`import Clash.Prelude` prelude swap; the `deriving stock (Generic, Show,
Eq)` and `deriving anyclass (NFDataX)` refrain stamped on every type so
Clash can put it in a register. Six posts have paid that toll in full ---
the [CRC][crc] and [primer][primer] posts derived it at length --- and I
will not charge it a seventh time. Everyone already knows what those are
about.

The one line worth stopping on is not ritual at all. It is the three
imports in the middle:

```haskell
import Tamal.Crc (crc8Update)
import Tamal.Loader.Cobs
import Tamal.Params (AW, RW)
```

The [CRC][crc] fold, the [COBS][cobs] codec, and the shared address
widths. The loader imports the very blocks the last six posts built and
puts them all to work at once. This is the confluence in three lines: read
them and you already know the loader will fold a CRC, stream a COBS codec,
and address two memories sized by `AW` and `RW`. Everything we have read
plugs in right here.

## Params, since it has nowhere else to live

One of those imports has no post of its own, and never will: `Tamal.Params`
is two type aliases and a doc-comment, and two lines do not earn a post of
their own. So we settle the small debt here, once, where the loader first
leans on them. In its entirety, minus the ritual:

```haskell
type AW = 10 :: Nat -- instruction-address width: 2^AW = 1024 words
type RW = 12 :: Nat -- ring/trace-address width: 2^RW = 4096 words
```

That is the whole module. `AW` is the instruction-address width --- `2^AW =
1024` words in the instruction store, and the program counter's width to
match. `RW` is the ring/trace-address width --- `2^RW = 4096` words in the
result ring, whose top slot is the reserved HALT terminator the drain
fetches last. The point is not the two numbers but the *sharing*:
`Tamal.Params` is a dependency-free leaf, importing only `Clash.Prelude`,
so the engine, the two BRAM wrappers, the trace model, and the loader can
all name the same widths without importing one another --- and widening the
instruction space or resizing the ring becomes a single edit here instead
of a hunt for hand-copied `Unsigned 10` and `Unsigned 12` literals
scattered across the tree. The loader meets both in a moment: `lAddr ::
Unsigned AW` counts instruction slots, `ringAddr :: Unsigned RW` sweeps the
ring. Debt paid; on to the types that do earn their keep.

## The types are the design

The [primer][primer]'s sum-and-product story, told one final time --- and
told carefully, because with the loader the **types are the design**. Read
the five of them and the machine is most of the way explained; the step
functions largely just honour what the records already promise. We take
them in the order they appear.

### The ports: `LoaderIn` and `LoaderOut`

```haskell
data LoaderIn = LoaderIn
  { rxByte :: Maybe (BitVector 8)
  , txReady :: Bool
  , halted :: Bool
  , ringPtrIn :: Unsigned RW
  , ringData :: BitVector 32
  }

data LoaderOut = LoaderOut
  { txByte :: Maybe (BitVector 8)
  , instrWr :: Maybe (Unsigned AW, BitVector 32)
  , ringAddr :: Unsigned RW
  , startOut :: Bool
  }
```

These two records *are* the seam. The [top][top] left four wires hanging
at the loader's door; here they are, named and typed, sitting beside the
wires that reach the other way, to the engine and the two memories. Read
`LoaderIn` as "everything the world hands the loader each cycle":

- **`rxByte`** --- a byte from the UART receiver, or `Nothing`. A
  one-cycle strobe: `Just b` on the cycle a byte lands, `Nothing` the
  ~500 cycles in between.
- **`txReady`** --- the UART transmitter is idle and can take a byte.
  Back-pressure for the drain.
- **`halted`** --- the engine's `haltedOut`, a *level*: high for as long
  as the engine sits in its `Halted` phase.
- **`ringPtrIn`** --- how many trace records the engine wrote. The drain's
  upper bound, read straight off the halted engine.
- **`ringData`** --- the ring BRAM's read data, arriving one cycle after
  the loader drives an address.

And `LoaderOut` is "everything the loader drives back":

- **`txByte`** --- a byte for the UART transmitter, or `Nothing`.
- **`instrWr`** --- a write to the instruction store: `Just (addr, word)`
  or nothing this cycle.
- **`ringAddr`** --- the ring BRAM read address the drain sweeps.
- **`startOut`** --- the one-cycle pulse that wakes the engine.

Two details hide in the signatures. First, the `Unsigned AW` and `Unsigned
RW` we just met surface here as concrete port widths --- `instrWr`'s
address into the `1024`-word instruction store, and `ringAddr`/`ringPtrIn`
into the `4096`-slot ring. Second, and quietly load-bearing: the loader
drives exactly **two** memory ports, the instruction *write* and the ring
*read*, and never the two the engine owns. It writes only while loading and
reads only while draining, never overlapping the engine's run --- so the two
machines share two BRAMs with no arbiter at all, collision-free by
construction.[^ports]

### `Lifecycle`: the three lives

```haskell
data Lifecycle = RxControl | Run | Drain
```

Three constructors, and the whole post's title. The loader is never doing
more than one of these at a time, and it moves between them in a fixed
loop:

- **`RxControl`** --- *listen.* The only life that consumes the UART's
  receive strobe. Decode each incoming control frame, load programs into
  the instruction store as they arrive, and watch for a trigger.
- **`Run`** --- *stand aside.* The engine is executing. The loader drives
  nothing, listens to nothing, and watches a single bit: `halted`.
- **`Drain`** --- *report.* The engine has halted; sweep its trace ring
  out the transmitter as one frame, then go back to listening.

The entire top of the machine is a `case` on this type. Load, run, drain,
and back to load --- three lives lived in a ring, re-runnable forever: a
later trigger re-runs the same program, a later load replaces it.

<figure class="life-fig" style="margin:2rem 0">
<svg class="life" viewBox="0 0 760 300" role="img" aria-labelledby="life-t life-d" xmlns="http://www.w3.org/2000/svg">
<title id="life-t">The loader's three-state lifecycle FSM</title>
<desc id="life-d">Three state boxes left to right: RxControl (listen for frames), Run (engine executes), and Drain (sweep the ring). An accent arrow from RxControl to Run is labelled valid TRIGGER, pulse startOut. A plain arrow from Run to Drain is labelled halted, a level. A long accent arc returns from Drain all the way back to RxControl, labelled drain complete, listen again. RxControl also carries a self-loop labelled valid LOAD_PROGRAM, words to the instruction BRAM, showing that loading a program leaves the machine in RxControl.</desc>
<style>
.life{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.life .st{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.life .wire{stroke:var(--fg-main);stroke-width:2;fill:none}
.life .hot{stroke:var(--accent);stroke-width:2.5;fill:none}
.life .loop{stroke:var(--fg-main);stroke-width:2;fill:none}
.life text{font-family:var(--sans)}
.life .name{fill:var(--fg-main);font-family:var(--mono);font-size:16px}
.life .sub{fill:var(--fg-dim);font-size:12px}
.life .lab{fill:var(--fg-main);font-size:12.5px}
.life .labA{fill:var(--accent);font-size:12.5px}
.life .ah{fill:var(--fg-main)}
.life .ahA{fill:var(--accent)}
</style>
<defs>
<marker id="life-a" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ah"/></marker>
<marker id="life-aa" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ahA"/></marker>
</defs>
<rect class="st" x="55"  y="120" width="180" height="70" rx="9"/>
<rect class="st" x="340" y="120" width="120" height="70" rx="9"/>
<rect class="st" x="545" y="120" width="175" height="70" rx="9"/>
<path class="loop" d="M95,120 C88,72 202,72 195,120" marker-end="url(#life-a)"/>
<line class="hot"  x1="235" y1="155" x2="336" y2="155" marker-end="url(#life-aa)"/>
<line class="wire" x1="460" y1="155" x2="541" y2="155" marker-end="url(#life-a)"/>
<path class="hot" d="M632,190 C632,258 145,258 145,192" marker-end="url(#life-aa)"/>
<text class="name" x="145" y="151" text-anchor="middle">RxControl</text>
<text class="sub"  x="145" y="172" text-anchor="middle">listen for frames</text>
<text class="name" x="400" y="151" text-anchor="middle">Run</text>
<text class="sub"  x="400" y="172" text-anchor="middle">engine executes</text>
<text class="name" x="632" y="151" text-anchor="middle">Drain</text>
<text class="sub"  x="632" y="172" text-anchor="middle">sweep the ring</text>
<text class="lab"  x="145" y="60"  text-anchor="middle">valid LOAD_PROGRAM · words → instr BRAM</text>
<text class="labA" x="286" y="112" text-anchor="middle">valid TRIGGER</text>
<text class="labA" x="286" y="178" text-anchor="middle">→ startOut</text>
<text class="lab"  x="500" y="112" text-anchor="middle">halted</text>
<text class="lab"  x="500" y="178" text-anchor="middle">(a level)</text>
<text class="labA" x="388" y="278" text-anchor="middle">drain complete → listen again</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The three lives. <code>RxControl</code> is the resting state and the only one that consumes the UART receiver; a valid <code>LOAD_PROGRAM</code> writes its words and stays put (self-loop), while a valid <code>TRIGGER</code> pulses <code>startOut</code> and steps to <code>Run</code>. <code>Run</code> watches the engine's <code>halted</code> level and, when it goes high, arms <code>Drain</code>. <code>Drain</code> sweeps the trace ring out the UART and returns to <code>RxControl</code> — so the rig is re-runnable, load/trigger/drain, round and round.</figcaption>
</figure>

### `DrainPhase`: a program counter for the sweep

```haskell
data DrainPhase = DrOpcode | DrFetch | DrLatch | DrWordByte | DrCrcByte | DrDrainOut | DrDelim
```

`Lifecycle` has three states because the loader has three jobs. `Drain`,
though, is not one action but a little *sequence* --- emit an opcode, then
fetch a ring word, then wait a cycle for the memory to answer, then emit
its four bytes, then loop, then emit a CRC, then flush, then cap the frame
--- and this seven-constructor sum is that sequence's program counter. The
`Drain` life runs a tiny program, and `lDrn` is where in the program it
is.

Why spell a sequence out as seven named states instead of a loop? Because
in hardware a loop *is* a state machine, and every step of this one is a
place the loader might have to **wait**: a BRAM read does not answer until
the next cycle, the UART will not take a byte unless `txReady`, and the
COBS encoder buffers a whole group before it emits. Each stall needs a
named state to hold in and come back to. We walk the seven in the drain
section; for now note the shape --- a fetch/latch pair for the memory's
latency, a per-byte emit state, and three states to close out the frame.

### `LoaderSt`: eighteen fields, four records in a trench coat

Here is the field that everyone flinches at:

```haskell
data LoaderSt = LoaderSt
  { lPhase :: Lifecycle
  , lDec :: DecSt
  , lEnc :: EncSt
  , lHeld :: Maybe (BitVector 8) -- one-byte holdback (separates the trailing CRC)
  , lCrcRx :: BitVector 8 -- running CRC over confirmed bytes
  , lHaveOp :: Bool -- opcode confirmed yet?
  , lOpcode :: BitVector 8
  , lByteIx :: Unsigned 2 -- payload byte within the current word (0..3)
  , lWordAcc :: BitVector 32 -- LE word being assembled
  , lHadPay :: Bool -- any payload byte seen (TRIGGER must have none)
  , lAddr :: Unsigned AW -- next instr write slot
  , lFull :: Bool -- instr store overflowed (> 2^AW words)
  , lDrn :: DrainPhase
  , lWord :: BitVector 32 -- ring word being emitted
  , lWIx :: Unsigned 2 -- LE byte of lWord (0..3)
  , lCrcTx :: BitVector 8 -- running CRC over the drain
  , lDrCnt :: Unsigned RW -- ring record index being fetched
  , lTerm :: Bool -- fetching/emitting the terminator word
  }
```

Eighteen fields is a lot, and the honest reaction is a wince. But it is
not eighteen unrelated things; it is **four small records wearing one
constructor**, because a `mealy` carries exactly one state value and the
loader would rather be one machine with one memory than four machines
haggling over turns. Sort the fields by which job they serve and the
wince goes away:

- **Phase (1 field).** `lPhase` --- which of the three lives we are living.
  The one field every cycle reads first.
- **Embedded codecs (2 fields).** `lDec` and `lEnc` --- the [COBS][cobs]
  decoder's `DecSt` and the encoder's `EncSt`, held *inside* the loader's
  state. This is the promise the last post made, kept to the letter: the
  codec exported its two opaque state types and their `initDec`/`initEnc`
  seeds precisely so the loader could carry them here and thread them
  through its own clock. "The loader is the machine that clocks them" ---
  these two fields are where.
- **RX / frame-parse (9 fields).** The load path's scratchpad: `lHeld`
  (the one-byte holdback), `lCrcRx` (running CRC over confirmed bytes),
  `lHaveOp` and `lOpcode` (have we seen the opcode, and what was it),
  `lByteIx` and `lWordAcc` (which of a word's four bytes we are on, and
  the little-endian word taking shape), `lHadPay` (did any payload arrive
  --- a `TRIGGER` must have none), and `lAddr` and `lFull` (the next
  instruction slot, and whether the store overflowed).
- **Drain (6 fields).** The report path's scratchpad: `lDrn` (the
  `DrainPhase` program counter), `lWord` and `lWIx` (the ring word being
  emitted and which of its bytes), `lCrcTx` (running CRC over the drain),
  `lDrCnt` (which ring record we are fetching), and `lTerm` (are we on the
  terminator word yet).

Read that way it is four modest records, and the two big ones --- RX-parse
and Drain --- are never live at once, since `RxControl` and `Drain` are
different lives. At any instant half these fields are asleep. They cohabit
one record for the reason the whole loader is one `mealy`: one machine
with one memory is simpler to reason about, and to register, than three
sharing a bus.

### The two one-liners

`initLoader` is the machine at power-on --- `RxControl`, both codecs seeded
with `initDec`/`initEnc`, every counter zero, every flag false: listening,
nothing held, nothing owed. `idleOut` is the do-nothing output --- no byte,
no write, no pulse --- and it earns a name because the loader returns it on
the overwhelming majority of cycles. Naming it once lets every step
function say "nothing happens this cycle" in five characters, and lets your
eye skip to the cycles that matter.

## The machine that owns the clock

```haskell
loader :: (HiddenClockResetEnable dom) => Signal dom LoaderIn -> Signal dom LoaderOut
loader = mealy loaderStep initLoader

loaderStep :: LoaderSt -> LoaderIn -> (LoaderSt, LoaderOut)
loaderStep s inp = case lPhase s of
  RxControl -> rxStep s inp
  Run -> runStep s inp
  Drain -> drainStep s inp
```

Here is the promise kept. Six posts of pure steps insisting *someone else
owns the register*, and this is the someone. `mealy` is the [Clash][clash]
primitive the [transmitter][tx] introduced: hand it a pure `s -> i -> (s,
o)` and a seed, and it wraps exactly one register around the state and
lifts the pure function into `Signal dom i -> Signal dom o` --- a clocked
thing. `loader` hands `mealy` its `loaderStep` and `initLoader`, and out
comes a signal function with a clock threaded through it. Every pure step
we praised for having no clock --- the CRC's, the codec's two --- gets its
clock *here*, because the loader clocks itself and carries them along in
`lDec`, `lEnc`, `lCrcRx`, `lCrcTx`.[^mealys]

And `loaderStep` is almost nothing: read the current life off `lPhase`
and delegate. That `case` is the whole top-level control flow --- the
three lives are three functions, and the machine is whichever the phase
names. So the rest of the post is three smaller ones; we take the easiest
first.

## Run: the life that waits

```haskell
runStep :: LoaderSt -> LoaderIn -> (LoaderSt, LoaderOut)
runStep s inp
  | halted inp =
      ( s{lPhase = Drain, lEnc = initEnc, lDrn = DrOpcode, lCrcTx = 0, lDrCnt = 0, lWIx = 0, lTerm = False}
      , idleOut
      )
  | otherwise = (s, idleOut)
```

The shortest of the three, and the only one that does essentially
nothing. While the engine runs, the loader has exactly one job: watch one
bit. `halted` is a *level*, not a pulse --- the engine's `Halted` phase is
stable, it does not blink --- so the guard just tests it every cycle. Low:
hold the state, emit `idleOut`. High: the run is over, so **arm the
drain** and switch lives.

Arming the drain is that record update, worth reading for what it spares.
It seeds the encoder fresh, rewinds the program counter to `DrOpcode`, and
zeroes the drain counters and CRC --- but pointedly does *not* touch
`lAddr`, `lFull`, or the program in the instruction BRAM. A drain must not
disturb the loaded code, because the rig is re-runnable: a later `TRIGGER`
with no new `LOAD` re-runs it. `Run` is a turnstile --- it spins until
`halted`, then clicks one notch into `Drain`.

## RxControl: the load path

`RxControl` is the loader listening. A control frame arrives on the UART
one byte at a time, and the job is to turn that trickle back into a
*message* --- an opcode and its payload --- verify it, and act. Before the
code, the thing being parsed. A frame on the wire looks like this, and so
does the frame the drain will later build in the other direction:

<figure class="frm-fig" style="margin:2rem 0">
<svg class="frm" viewBox="0 0 760 300" role="img" aria-labelledby="frm-t frm-d" xmlns="http://www.w3.org/2000/svg">
<title id="frm-t">The control frame and the result frame, layer by layer</title>
<desc id="frm-d">Two frames of identical shape. The control frame (host to FPGA) is an opcode byte (0x01 LOAD or 0x02 TRIGGER), a payload of little-endian words or none, and a CRC-8 byte — that trio COBS-encoded so it holds no interior zero — followed by a single 0x00 delimiter. The result frame (FPGA to host) is the same shape: opcode 0x81, the ring's trace words (REVISION, records, terminator), a CRC-8, COBS-encoded, then 0x00. In both, the message (opcode plus payload) is the Tamal.Wire layer; the CRC and the delimiter are the frame layer; COBS is the byte-stuffing layer from the previous post.</desc>
<style>
.frm{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.frm .box{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.frm .delim{fill:none;stroke:var(--accent);stroke-width:2;stroke-dasharray:5 4}
.frm .brace{stroke:var(--fg-dim);stroke-width:1.5;fill:none}
.frm text{font-family:var(--sans)}
.frm .val{fill:var(--fg-main);font-family:var(--mono);font-size:14px}
.frm .valA{fill:var(--accent);font-family:var(--mono);font-size:14px}
.frm .lab{fill:var(--fg-dim);font-size:12px}
.frm .labA{fill:var(--accent);font-size:12.5px}
.frm .sub{fill:var(--fg-dim);font-size:11.5px}
</style>
<text class="lab" x="24" y="103" text-anchor="start">control</text>
<rect class="box"   x="95"  y="76" width="95"  height="46" rx="5"/>
<rect class="box"   x="190" y="76" width="205" height="46" rx="5"/>
<rect class="box"   x="395" y="76" width="85"  height="46" rx="5"/>
<rect class="delim" x="560" y="76" width="70"  height="46" rx="5"/>
<path class="brace" d="M95,68 V58 H480 V68"/>
<path class="brace" d="M95,130 V140 H395 V130"/>
<text class="valA" x="142" y="98"  text-anchor="middle">opcode</text>
<text class="sub"  x="142" y="114" text-anchor="middle">0x01 / 0x02</text>
<text class="val"  x="292" y="98"  text-anchor="middle">payload</text>
<text class="sub"  x="292" y="114" text-anchor="middle">LE words · or none</text>
<text class="val"  x="437" y="104" text-anchor="middle">crc8</text>
<text class="valA" x="595" y="104" text-anchor="middle">00</text>
<text class="labA" x="287" y="50"  text-anchor="middle">COBS-encoded — no interior 0x00 (last post)</text>
<text class="lab"  x="245" y="156" text-anchor="middle">message: opcode ++ payload  (Tamal.Wire)</text>
<text class="lab"  x="437" y="156" text-anchor="middle">+ CRC-8</text>
<text class="lab"  x="595" y="156" text-anchor="middle">+ delimiter</text>
<text class="lab" x="24" y="237" text-anchor="start">result</text>
<rect class="box"   x="95"  y="210" width="95"  height="46" rx="5"/>
<rect class="box"   x="190" y="210" width="205" height="46" rx="5"/>
<rect class="box"   x="395" y="210" width="85"  height="46" rx="5"/>
<rect class="delim" x="560" y="210" width="70"  height="46" rx="5"/>
<path class="brace" d="M95,202 V192 H480 V202"/>
<text class="valA" x="142" y="232" text-anchor="middle">opcode</text>
<text class="sub"  x="142" y="248" text-anchor="middle">0x81</text>
<text class="val"  x="292" y="232" text-anchor="middle">trace words</text>
<text class="sub"  x="292" y="248" text-anchor="middle">REV · records · term</text>
<text class="val"  x="437" y="238" text-anchor="middle">crc8</text>
<text class="valA" x="595" y="238" text-anchor="middle">00</text>
<text class="labA" x="287" y="184" text-anchor="middle">COBS-encoded</text>
<text class="lab"  x="388" y="286" text-anchor="middle">both directions, one shape:  COBS( opcode ++ payload ++ crc8 ) ++ 0x00</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">One frame shape, both directions. Read it from the inside out: the <em>message</em> — an opcode and its payload — is the <a href="https://github.com/felipebalbi/tamal">Tamal.Wire</a> layer; a <code>CRC-8</code> byte guards it; <a href="https://balbi.sh/posts/tamal-loader-cobs/">COBS</a> stuffs every zero out of the opcode-payload-CRC run; and a lone <code>0x00</code> delimiter closes the frame. The last post built the COBS layer. This post builds the two layers outside it — and peels them, on the way in.</figcaption>
</figure>

Read from the inside out. The innermost thing is the *message* --- an
opcode byte and its payload, the logical frame `Tamal.Wire`'s
`encodeControl` builds before it wraps it. Around it, a `CRC-8` byte guards
the lot. Around *that*, [COBS][cobs] stuffs out every zero, so the run
holds no interior `0x00`. And around everything, a single `0x00` delimiter.
The loader's receive job is to peel those layers in reverse: watch for the
delimiter, un-COBS the interior, check the CRC, and read the message. Here
is the whole of it.

```haskell
rxStep :: LoaderSt -> LoaderIn -> (LoaderSt, LoaderOut)
rxStep s inp =
  let din = case rxByte inp of
        Just 0 -> (Nothing, True) -- delimiter => frame end
        Just b -> (Just b, False)
        Nothing -> (Nothing, False)
      (dec', (mDec, done, bad)) = cobsDecodeStep (lDec s) din
      s1 = s{lDec = dec'}
   in if done
        then finalize s1 bad
        else case mDec of
          Nothing -> (s1, idleOut)
          Just d ->
            let (s2, mw) = case lHeld s1 of
                  Just h -> confirm s1 h
                  Nothing -> (s1, Nothing)
             in (s2{lHeld = Just d}, idleOut{instrWr = mw})
```

Four moves, top to bottom. The **delimiter watch** comes first: that
`case rxByte`. A received `0x00` is not data --- COBS guarantees no interior
zeros, so a zero on the wire can only be the frame boundary --- so the
loader translates it into the codec's vocabulary: `Just 0` becomes
`(Nothing, frameEnd = True)`, any other byte `(Just b, False)`, no byte
`(Nothing, False)`. That `frameEnd` pulse is exactly the one
[`cobsDecodeStep`][cobs] expects, and here is the thing that pulses it. The
delimiter belongs to the frame layer, not the codec, and this `case` is
where that line is drawn.

Second, **feed the codec.** `cobsDecodeStep` runs, threaded through
`lDec`, and hands back the triple we read last post: a maybe-decoded byte
`mDec`, a `done` pulse, a `bad` flag.

Third, if `done` --- the delimiter fired --- the frame is over; hand off to
`finalize`. If instead the codec produced nothing this cycle (`Nothing`),
idle; that is the common case, a byte mid-flight or a code byte that only
armed a counter.

And fourth, the move that deserves its own name. When a decoded byte `d`
*does* arrive, the loader does **not** process it. It processes the byte
it was already holding, `lHeld`, and stashes `d` in its place. That is the
**holdback**, and it exists to solve one specific problem:

> The last byte of the logical stream is the CRC. But nothing marks it as
> the CRC while it streams --- it looks like any other byte, right up until
> the delimiter proves it was the last one. So the loader always lags one
> byte behind: it holds the newest decoded byte and only *confirms* the
> previous one, because only a byte with another byte behind it is
> provably payload, not the trailing check.

<figure class="rxf-fig" style="margin:2rem 0">
<svg class="rxf" viewBox="0 0 760 312" role="img" aria-labelledby="rxf-t rxf-d" xmlns="http://www.w3.org/2000/svg">
<title id="rxf-t">The RX pipeline and the one-byte holdback</title>
<desc id="rxf-d">Top: a pipeline. rxByte flows into a delimiter-watch box, then a COBS decode box, then an accent-highlighted holdback box (lHeld), then a confirm box, then out to instrWr and startOut. Bottom: the decoded logical stream drawn as five boxes — op, p0, p1, p2, and a dashed accent crc. A bracket under the first four says each byte is confirmed — CRC-folded and routed — only when the next byte lands. The final crc box is marked as the candidate, never confirmed, checked at the 0x00 delimiter instead.</desc>
<style>
.rxf{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.rxf .box{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.rxf .hot{fill:var(--bg-dim);stroke:var(--accent);stroke-width:2.5}
.rxf .cand{fill:none;stroke:var(--accent);stroke-width:2;stroke-dasharray:5 4}
.rxf .wire{stroke:var(--fg-main);stroke-width:2;fill:none}
.rxf .brace{stroke:var(--accent);stroke-width:2;fill:none}
.rxf .div{stroke:var(--fg-dim);stroke-width:1;stroke-dasharray:4 4}
.rxf text{font-family:var(--sans)}
.rxf .val{fill:var(--fg-main);font-family:var(--mono);font-size:15px}
.rxf .valA{fill:var(--accent);font-family:var(--mono);font-size:15px}
.rxf .lab{fill:var(--fg-main);font-size:12.5px}
.rxf .sub{fill:var(--fg-dim);font-size:11.5px}
.rxf .note{fill:var(--accent);font-size:12px}
.rxf .ah{fill:var(--fg-main)}
</style>
<defs>
<marker id="rxf-a" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ah"/></marker>
</defs>
<text class="sub" x="40" y="75" text-anchor="start">rxByte</text>
<rect class="box" x="110" y="48" width="100" height="46" rx="5"/>
<rect class="box" x="250" y="48" width="100" height="46" rx="5"/>
<rect class="hot" x="390" y="48" width="100" height="46" rx="5"/>
<rect class="box" x="530" y="48" width="95"  height="46" rx="5"/>
<line class="wire" x1="80"  y1="71" x2="108" y2="71" marker-end="url(#rxf-a)"/>
<line class="wire" x1="210" y1="71" x2="248" y2="71" marker-end="url(#rxf-a)"/>
<line class="wire" x1="350" y1="71" x2="388" y2="71" marker-end="url(#rxf-a)"/>
<line class="wire" x1="490" y1="71" x2="528" y2="71" marker-end="url(#rxf-a)"/>
<line class="wire" x1="625" y1="71" x2="658" y2="71" marker-end="url(#rxf-a)"/>
<text class="lab" x="160" y="67" text-anchor="middle">delimiter</text>
<text class="sub" x="160" y="83" text-anchor="middle">watch 0x00</text>
<text class="lab" x="300" y="67" text-anchor="middle">COBS</text>
<text class="sub" x="300" y="83" text-anchor="middle">decode</text>
<text class="valA" x="440" y="67" text-anchor="middle">holdback</text>
<text class="sub"  x="440" y="83" text-anchor="middle">lHeld</text>
<text class="lab" x="577" y="76" text-anchor="middle">confirm</text>
<text class="sub" x="700" y="67" text-anchor="middle">instrWr /</text>
<text class="sub" x="700" y="83" text-anchor="middle">startOut</text>
<line class="div" x1="40" y1="120" x2="720" y2="120"/>
<text class="sub" x="40" y="150" text-anchor="start">decoded logical stream, always one byte behind:</text>
<rect class="box"  x="120" y="175" width="68" height="48" rx="5"/>
<rect class="box"  x="228" y="175" width="68" height="48" rx="5"/>
<rect class="box"  x="336" y="175" width="68" height="48" rx="5"/>
<rect class="box"  x="444" y="175" width="68" height="48" rx="5"/>
<rect class="cand" x="552" y="175" width="68" height="48" rx="5"/>
<text class="val"  x="154" y="205" text-anchor="middle">op</text>
<text class="val"  x="262" y="205" text-anchor="middle">p0</text>
<text class="val"  x="370" y="205" text-anchor="middle">p1</text>
<text class="val"  x="478" y="205" text-anchor="middle">p2</text>
<text class="valA" x="586" y="205" text-anchor="middle">crc</text>
<path class="brace" d="M120,233 V243 H512 V233"/>
<text class="note" x="316" y="262" text-anchor="middle">confirmed — CRC-folded, routed — only when the next byte lands</text>
<line class="brace" x1="586" y1="225" x2="586" y2="248" stroke-dasharray="5 4"/>
<text class="note" x="586" y="266" text-anchor="middle">candidate</text>
<text class="sub"  x="586" y="281" text-anchor="middle">checked at 0x00</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The receive pipeline and its one-byte lag. Bytes flow <code>rxByte → delimiter-watch → COBS decode → holdback → confirm</code>. Because the trailing byte of the logical stream is the CRC — indistinguishable from payload until the frame ends — the loader confirms each byte only when the <em>next</em> one arrives. The final byte is never confirmed: it is the CRC candidate, held back and checked against the running CRC when the <code>0x00</code> delimiter closes the frame.</figcaption>
</figure>

Same field, two fates: when a new byte arrives, the held one is provably
not the last, so `confirm` works on it; when the delimiter arrives, the
held byte *is* the last, so `finalize` treats it as the CRC. What comes
next decides.[^holdback]

### `confirm`: fold, route, assemble, write through

`confirm` receives a byte `h` that has just been *proven* payload --- a
byte arrived behind it --- and does the frame's real bookkeeping (its full
text is in the listing above). Two cases split on whether we have an
opcode yet.

The **first confirmed byte is the opcode.** Record it in `lOpcode`, set
`lHaveOp`, and fold it into the running CRC. Then one special touch: if the
opcode is `0x01`, `LOAD_PROGRAM`, reset the write address to `0` and clear
the overflow flag, so a load always writes from the top of the store. No
output byte --- an opcode writes nothing to memory.

Every **later confirmed byte is payload.** Fold it into the CRC, then
shift it into `lWordAcc` at the current byte position: ``zeroExtend h
`shiftL` (8 * byteIx)``. That is little-endian assembly by construction ---
byte 0 lands in bits 7:0, byte 1 in 15:8, and so on. When `lByteIx` reaches
`3` a full 32-bit word is complete, and *this* is where the load actually
touches memory:

```haskell
      if lByteIx s == 3
        then
          let doWrite = isLoad && not (lFull s)
              (addr', full') = if lAddr s == maxBound then (lAddr s, True) else (lAddr s + 1, lFull s)
           in ( s{ lCrcRx = crc', lWordAcc = 0, lByteIx = 0, lHadPay = True
                 , lAddr = if isLoad then addr' else lAddr s
                 , lFull = if isLoad then full' else lFull s }
              , if doWrite then Just (lAddr s, acc') else Nothing )
        else
          ( s{lCrcRx = crc', lWordAcc = acc', lByteIx = lByteIx s + 1, lHadPay = True}, Nothing )
```

The completed word is emitted as `Just (lAddr s, acc')` --- a write to the
instruction BRAM at the current slot --- provided this is a `LOAD` and the
store has not overflowed. Then the address bumps (saturating at `maxBound`
--- `1023` for `Unsigned AW` --- and latching `lFull` rather than wrapping),
the accumulator and byte index reset, and `lHadPay` records that payload
was seen. A word that is not a load, or that lands past the 1024-word cap,
just advances the state and writes nothing.

Notice that the write to the instruction BRAM happens here, *mid frame*,
before the CRC is ever checked. The design calls it **write-through**:
rather than buffer the program to commit it atomically, the loader
scribbles each word into the store the instant it assembles. A frame that
later fails its CRC has already dirtied memory --- and that is fine,
because loading and triggering are *separate frames*: nothing runs until a
good `TRIGGER`, and a bad `LOAD` is overwritten by the host's retry before
any trigger ever fires.[^writethrough]

### `finalize`: the delimiter's verdict

```haskell
finalize :: LoaderSt -> Bool -> (LoaderSt, LoaderOut)
finalize s bad =
  let crcCand = fromMaybe 0 (lHeld s)
      crcGood = not bad && isJust (lHeld s) && lHaveOp s && lCrcRx s == crcCand
      trigOk = crcGood && lOpcode s == 0x02 && not (lHadPay s)
      s0 = resetFrame s
   in if trigOk
        then (s0{lPhase = Run}, idleOut{startOut = True})
        else (s0, idleOut)
```

The delimiter fired, so the held byte is not payload --- there is no byte
behind it --- which makes it the **CRC candidate**, `crcCand`. Now the
verdict, in two conjunctions.

`crcGood` demands four things at once: the codec did not report the frame
`bad` (no truncated group, no overshoot); a byte was actually held (a
frame with nothing in it holds `Nothing`); an opcode was seen; and the CRC
folded over every confirmed byte equals the candidate. Miss any one and
the frame is junk. `trigOk` narrows further: a good frame whose opcode is
`0x02`, `TRIGGER`, carrying no payload. Only that pulses the engine.

Then the machine resets its frame-parse state either way (`resetFrame`
clears the codec, holdback, CRC, opcode, and word assembly --- but *not*
`lAddr`/`lFull`, already committed by the write-through) and branches. A
good trigger flips to `Run` and pulses `startOut` for one cycle. Anything
else --- a good `LOAD` (words already in memory), a bad frame, an unknown
opcode --- resets and stays in `RxControl`, silent. That silence is the
whole error policy: the loader never NAKs, never raises an error frame; a
bad frame simply has no effect, and the host, hearing nothing, times out
and re-sends.[^noflow]

### Watching a TRIGGER land

Take the smallest interesting frame --- a `TRIGGER` --- and feed it byte by
byte. Its logical stream is one opcode, `0x02`, plus its CRC-8, which for
the single byte `0x02` works out to `0x0E`. [COBS][cobs] wraps the pair
`02 0E` (no zeros) as a single group `03 02 0E`, and the frame layer caps
it with the delimiter. On the wire: `03 02 0E 00`.

1. **`03`** --- delimiter-watch passes `(Just 3, False)`; `cobsDecodeStep`
   reads a code byte, arms its counter, emits nothing. `lHeld` is still
   `Nothing`. Idle.
2. **`02`** --- the decoder emits the data byte `02`. `lHeld` is `Nothing`,
   so nothing is confirmed yet; stash `lHeld = Just 02`. Idle.
3. **`0E`** --- the decoder emits `0E`. Now `lHeld` holds `02`, so
   `confirm` runs on it: it is the first byte, so `lOpcode = 0x02`,
   `lHaveOp = True`, and `lCrcRx = crc8Update 0 0x02 = 0x0E`. Then stash
   `lHeld = Just 0E`. Idle. *The opcode is confirmed; the CRC byte is held.*
4. **`00`** --- delimiter. `cobsDecodeStep` pulses `done`, not `bad`.
   `finalize`: `crcCand = 0E`; `crcGood` is `True` because `lCrcRx = 0E`
   equals it, an opcode was seen, a byte was held, nothing was malformed.
   `trigOk` is `True` --- opcode `0x02`, no payload. **Pulse `startOut`,
   step to `Run`.**

Read the drama in the last two steps. The held `0E` was *never* confirmed
as payload --- it had no byte behind it --- so it stayed the candidate and
was checked, not folded. The opcode `02` *was* confirmed, at the exact
moment `0E` arrived to prove it was not the last byte. The holdback did its
one job perfectly: the opcode got folded, the CRC got checked, and the
pulse fired after the delimiter, never a cycle before the frame was known
good. Corrupt any byte of `03 02 0E 00` and step 4's `crcGood` collapses;
no pulse, no run --- exactly what a fire-and-forget link wants.

## Drain: the result path

`Drain` is the mirror image. Where `RxControl` peeled a frame off the
wire, `Drain` builds one and pushes it out --- the result frame from the
diagram above: opcode `0x81`, then the ring's words as little-endian bytes
(the REVISION at word 0, the engine's trace records, and the HALT
terminator at the very top of the ring), then a CRC, all COBS-encoded and
capped with a delimiter.

But the drain is harder than the load, and for a nameable reason: three
different things can make it **wait**. A BRAM read takes a cycle to
answer. The UART accepts a byte only when `txReady`. The COBS encoder
buffers a whole group before it emits. Every one of those is a stall, and
a stall needs a state to wait in and return from. That is why `Drain` is
not a loop but the seven-state sub-machine `DrainPhase` named earlier ---
one state per place the sweep might have to pause.

<figure class="drn-fig" style="margin:2rem 0">
<svg class="drn" viewBox="0 0 760 210" role="img" aria-labelledby="drn-t drn-d" xmlns="http://www.w3.org/2000/svg">
<title id="drn-t">The seven-phase drain sequence</title>
<desc id="drn-d">Seven phases left to right: DrOpcode (emit 0x81), DrFetch (drive ring address) and DrLatch (capture ring data) highlighted as the one-cycle BRAM-latency pair, DrWordByte (emit four little-endian bytes), DrCrcByte (emit the CRC, flagged last), DrDrainOut (flush the encoder), and DrDelim (emit the 0x00 delimiter, then return to RxControl). An accent loop runs from DrWordByte back to DrFetch for each further record; a bracket marks DrFetch and DrLatch as the BRAM latency pair; a note says every byte is paced by txReady.</desc>
<style>
.drn{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.drn .st{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.drn .pair{fill:var(--bg-dim);stroke:var(--accent);stroke-width:2.5}
.drn .wire{stroke:var(--fg-main);stroke-width:2;fill:none}
.drn .hot{stroke:var(--accent);stroke-width:2.5;fill:none}
.drn .brace{stroke:var(--fg-dim);stroke-width:1.5;fill:none}
.drn text{font-family:var(--sans)}
.drn .name{fill:var(--fg-main);font-family:var(--mono);font-size:12px}
.drn .sub{fill:var(--fg-dim);font-size:10.5px}
.drn .lab{fill:var(--fg-main);font-size:11.5px}
.drn .labA{fill:var(--accent);font-size:11.5px}
.drn .ah{fill:var(--fg-main)}
.drn .ahA{fill:var(--accent)}
</style>
<defs>
<marker id="drn-a" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ah"/></marker>
<marker id="drn-aa" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ahA"/></marker>
</defs>
<rect class="st"   x="20"  y="95" width="94"  height="52" rx="7"/>
<rect class="pair" x="126" y="95" width="78"  height="52" rx="7"/>
<rect class="pair" x="216" y="95" width="78"  height="52" rx="7"/>
<rect class="st"   x="306" y="95" width="106" height="52" rx="7"/>
<rect class="st"   x="424" y="95" width="92"  height="52" rx="7"/>
<rect class="st"   x="528" y="95" width="104" height="52" rx="7"/>
<rect class="st"   x="644" y="95" width="90"  height="52" rx="7"/>
<line class="wire" x1="0"   y1="121" x2="18"  y2="121" marker-end="url(#drn-a)"/>
<line class="wire" x1="114" y1="121" x2="124" y2="121" marker-end="url(#drn-a)"/>
<line class="wire" x1="204" y1="121" x2="214" y2="121" marker-end="url(#drn-a)"/>
<line class="wire" x1="294" y1="121" x2="304" y2="121" marker-end="url(#drn-a)"/>
<line class="wire" x1="412" y1="121" x2="422" y2="121" marker-end="url(#drn-a)"/>
<line class="wire" x1="516" y1="121" x2="526" y2="121" marker-end="url(#drn-a)"/>
<line class="wire" x1="632" y1="121" x2="642" y2="121" marker-end="url(#drn-a)"/>
<path class="hot" d="M330,95 C330,50 185,50 185,93" marker-end="url(#drn-aa)"/>
<path class="brace" d="M126,153 V163 H294 V153"/>
<text class="name" x="67"  y="119" text-anchor="middle">DrOpcode</text>
<text class="sub"  x="67"  y="135" text-anchor="middle">0x81</text>
<text class="name" x="165" y="119" text-anchor="middle">DrFetch</text>
<text class="sub"  x="165" y="135" text-anchor="middle">addr</text>
<text class="name" x="255" y="119" text-anchor="middle">DrLatch</text>
<text class="sub"  x="255" y="135" text-anchor="middle">data</text>
<text class="name" x="359" y="119" text-anchor="middle">DrWordByte</text>
<text class="sub"  x="359" y="135" text-anchor="middle">×4 LE bytes</text>
<text class="name" x="470" y="119" text-anchor="middle">DrCrcByte</text>
<text class="sub"  x="470" y="135" text-anchor="middle">CRC · last</text>
<text class="name" x="580" y="119" text-anchor="middle">DrDrainOut</text>
<text class="sub"  x="580" y="135" text-anchor="middle">flush enc</text>
<text class="name" x="689" y="119" text-anchor="middle">DrDelim</text>
<text class="sub"  x="689" y="135" text-anchor="middle">0x00</text>
<text class="sub"  x="67"  y="82"  text-anchor="middle">from Run</text>
<text class="labA" x="257" y="42"  text-anchor="middle">more records → next word</text>
<text class="lab"  x="210" y="180" text-anchor="middle">1-cycle BRAM latency</text>
<text class="labA" x="689" y="166" text-anchor="middle">→ RxControl</text>
<text class="lab"  x="560" y="180" text-anchor="middle">every byte paced by txReady</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The drain as a seven-step program. <code>DrOpcode</code> emits <code>0x81</code>; the accent <code>DrFetch</code>/<code>DrLatch</code> pair drives a ring address and captures the data one cycle later; <code>DrWordByte</code> emits the word's four little-endian bytes and loops back to <code>DrFetch</code> for each further record; <code>DrCrcByte</code> emits the drain CRC flagged <em>last</em>; <code>DrDrainOut</code> clocks the encoder until its final group is flushed; and <code>DrDelim</code> appends the lone <code>0x00</code> and returns to <code>RxControl</code>. Every emitted byte waits for <code>txReady</code> — the self-pacing a flow-control-less link leans on.</figcaption>
</figure>

The sub-machine is one `case` on `lDrn`, shown in full in the listing
above. Three of its arms --- `DrOpcode`, `DrWordByte`, `DrCrcByte` --- are
one-liners that hand a logical byte to `feedByte` (next section); the
other four move memory and manage the encoder. Its only real branching is
the `where`-helper that decides where to go after each emitted word byte:

```haskell
  afterWordByte i s'
    | lWIx s' /= 3 = s'{lWIx = lWIx s' + 1}
    | lTerm s' = s'{lDrn = DrCrcByte}
    | lDrCnt s' + 1 >= ringPtrIn i = s'{lTerm = True, lDrn = DrFetch}
    | otherwise = s'{lDrCnt = lDrCnt s' + 1, lDrn = DrFetch}
```

Walk the seven, remembering that the three feeding phases *produce* the
logical stream while the other four move memory and manage the encoder.

- **`DrOpcode`** --- feed the opcode `0x81`; when the encoder takes it,
  advance to `DrFetch` and zero the record counter. The frame begins.
- **`DrFetch`** --- drive the ring read address (`lDrCnt`, or `maxBound`
  for the terminator) onto `ringAddr`. The BRAM will answer *next* cycle,
  so this state does nothing else but keep the encoder turning (fed
  `Nothing`) in case it still has buffered bytes to emit. Advance to
  `DrLatch`. This is the *address* half of a read.
- **`DrLatch`** --- the BRAM's answer is on `ringData` now; latch it into
  `lWord`, reset the byte cursor `lWIx = 0`, advance to `DrWordByte`. The
  *data* half. Fetch-then-latch, two states, is exactly the block RAM's
  one-cycle read latency written out --- a contract the two memories get
  their own post next to explain, honoured here as a pair of phases.
- **`DrWordByte`** --- feed the current little-endian byte of `lWord`
  (via `leByte`) to the encoder; on consume, run `afterWordByte`. That
  helper is the drain's only real branching: if bytes remain in this word
  (`lWIx /= 3`), bump the cursor; else if this was the terminator, go to
  `DrCrcByte`; else if the record just fetched was the last one
  (`lDrCnt + 1 >= ringPtrIn`), set `lTerm` and loop to `DrFetch` for the
  terminator at `maxBound`; else bump `lDrCnt` and loop to `DrFetch` for
  the next record. That is the accent loop in the diagram.
- **`DrCrcByte`** --- feed the accumulated drain CRC `lCrcTx`, flagged
  `last` so the encoder knows the logical stream has ended and can flush
  its final group. Advance to `DrDrainOut`.
- **`DrDrainOut`** --- no more input; just clock the encoder (`Nothing`)
  until it pulses `encDone`, meaning its last buffered group is out. Then
  `DrDelim`.
- **`DrDelim`** --- append the one `0x00` frame delimiter, paced on
  `txReady`, reset the frame state, and return to `RxControl`. The frame
  is complete; the loader is listening again.

One quiet elegance is worth pausing on. `DrFetch` and `DrLatch` re-use
`lWord` for every record, overwriting it each time --- safe, because by the
time the machine leaves `DrWordByte` all four of the old word's bytes are
already *inside* the encoder's buffer. The word register is a one-word
window; the encoder is the real buffer, and its 254-byte look-ahead
decouples the fast BRAM sweep from the far slower UART emit.

### `feedByte`: one byte to the encoder

```haskell
feedByte ::
  LoaderSt -> LoaderIn -> BitVector 8 -> Bool -> (LoaderSt -> LoaderSt) -> (LoaderSt, LoaderOut)
feedByte s inp b lst advance =
  let (enc', (readyIn, mOut, _)) = cobsEncodeStep (lEnc s) (Just (b, lst), txReady inp)
      s1 = s{lEnc = enc'}
      s2 =
        if readyIn
          then advance s1{lCrcTx = if lst then lCrcTx s1 else crc8Update (lCrcTx s1) b}
          else s1
   in (s2, idleOut{txByte = mOut})
```

`feedByte` is the drain's workhorse, and the exact dual of the receive
side's fold-and-route. It presents one *logical* byte `b` to
[`cobsEncodeStep`][cobs] along with its `last` flag and the downstream-ready
line `txReady`. The encoder answers with `readyIn` (did it take the
byte?), `mOut` (a COBS byte to send, maybe), and a done flag we ignore
here.

If `readyIn` --- the byte was consumed --- fold it into the drain CRC
(*unless* it is the CRC byte itself, flagged `lst`; you do not CRC the
CRC) and run the `advance` continuation to step the phase. If not consumed
--- the encoder is mid-group, or `txReady` is low --- hold: same byte, same
phase, next cycle. Either way, route `mOut` to `txByte`. So every output
byte is gated on `txReady`, and the generator runs exactly as fast as the
encoder consumes, which runs exactly as fast as the UART drains.[^noflow]
The separation is the pretty part: the three feeding phases decide *which
byte comes next*; `feedByte` owns *how to hand it over and when to
advance*. Three call sites, one rule.

### `leByte`: the little-endian tap

```haskell
leByte :: BitVector 32 -> Unsigned 2 -> BitVector 8
leByte w i = case i of
  0 -> slice d7 d0 w
  1 -> slice d15 d8 w
  2 -> slice d23 d16 w
  _ -> slice d31 d24 w
```

The trivial helper, listed for symmetry: byte `i` of a 32-bit word,
little-endian --- `slice d7 d0` is the low byte, up to `slice d31 d24` the
high. It is the exact inverse of the receive side's ``lWordAcc `shiftL`
(8 * byteIx)``: the load *packs* four little-endian bytes into a word, the
drain *unpacks* a word into four. The wire is little-endian in both
directions, and these two lines are the two ends of that one agreement.

### Watching the minimal drain

The smallest possible drain is a ring with no records at all --- just the
REVISION word and the terminator. Say `ringPtrIn = 1`, `ring[0] =
0x0001_0000` (REVISION), and the terminator at the top of the ring is
`0xC000_0000`. The logical stream the phases produce should be `0x81`, then
the four little-endian bytes of each word, then the CRC:

1. **`DrOpcode`** --- feed `0x81`; consumed, so fold it into `lCrcTx`, go
   to `DrFetch`, `lDrCnt = 0`.
2. **`DrFetch`** --- drive `ringAddr = 0`. → `DrLatch`.
3. **`DrLatch`** --- latch `lWord = 0x0001_0000`, `lWIx = 0`. → `DrWordByte`.
4. **`DrWordByte` ×4** --- feed `00 00 01 00`, the LE bytes of
   `0x0001_0000`. After the fourth, `lWIx` was `3`, not the terminator,
   and `lDrCnt + 1 = 1 >= ringPtrIn = 1`, so set `lTerm` and loop to
   `DrFetch`.
5. **`DrFetch`** (terminator) --- drive `ringAddr = maxBound`. → `DrLatch`.
6. **`DrLatch`** --- latch `lWord = 0xC000_0000`, `lWIx = 0`. → `DrWordByte`.
7. **`DrWordByte` ×4** --- feed `00 00 00 C0`. After the fourth, `lWIx = 3`
   and `lTerm` is set, so → `DrCrcByte`.
8. **`DrCrcByte`** --- feed `lCrcTx`, the CRC folded over `0x81` and all
   eight word bytes, flagged `last`. → `DrDrainOut`.
9. **`DrDrainOut`** --- clock the encoder until `encDone`. → `DrDelim`.
10. **`DrDelim`** --- emit `0x00`, reset, → `RxControl`.

The logical stream handed to the encoder is `81 · 00 00 01 00 · 00 00 00
C0 · crc` --- ten bytes, and notice how thoroughly zero-riddled it is. A
real ring word is full of `0x00`, which is *exactly* why COBS is in this
pipeline at all: the encoder stuffs every one of those zeros out, the loader
caps the result with a single `0x00`, and the whole thing comes out equal,
byte for byte, to `Tamal.Wire`'s pure `encodeResult [0x0001_0000,
0xC000_0000]`. Which is precisely what the test suite asserts --- so let us
turn to it.

## The tests

The loader is impure, so unlike the pure COBS steps you cannot apply it to
a value and read the answer --- you build a `Signal`, `sampleN` it, and
compare. But the *discipline* is the [COBS post][cobs]'s, up one layer: a
pure reference is the oracle, and the streaming machine is held to it byte
for byte. Where COBS checked itself against pure `Tamal.Wire.Cobs`, the
loader checks its whole frame layer against pure `Tamal.Wire` ---
`encodeControl` and `encodeResult`, the list-to-list functions that *are*
the wire format.

The load path tests feed the exact bytes of `encodeControl (LoadProgram
ws)` and assert the write stream:

```haskell
testProperty "LOAD_PROGRAM writes the exact words at 0,1,2,.." $ property $ do
  ws <- forAll (Gen.list (Range.linear 0 20) genWord)
  let bytes = encodeControl (LoadProgram ws)
  simInstrWr (fmap Just bytes) === [(fromIntegral i, w) | (i, w) <- L.zip [0 :: Int ..] ws]
```

For random programs, the words land at `0, 1, 2, …` with the right
values --- the write-through, the little-endian assembly, and the holdback
all proven in one line. A `TRIGGER` pulses `startOut` exactly once and only
after the frame; a `LOAD` pulses it never. There is one lovely detail in
the harness: every stimulus is led by an idle cycle, because `sampleN`
asserts the reset on cycle 0 and a byte fed then would be lost --- *exactly
as it would be lost in hardware*, where the line idles before the first
frame. The test models the reset hazard rather than papering over it.

The drain tests are the clever ones, because the drain reads a memory the
test must supply. The rig closes the ring-BRAM loop with a `register` for
the one-cycle latency and a pure `ringModel` as the memory:

```haskell
drainRig lookupRing ringPtrV rxs txr hlt = txByte <$> loaderOut
 where
  loaderOut = loader loaderIn
  ringDataS = register 0 (lookupRing <$> (ringAddr <$> loaderOut))
  loaderIn  = LoaderIn <$> rxs <*> txr <*> hlt <*> pure ringPtrV <*> ringDataS
```

That `register 0 (lookupRing <$> ringAddr)` *is* the BRAM: the loader's own
`ringAddr` output, delayed one cycle and looked up in the model, becomes
its `ringData` input --- the fetch/latch dance closed into a feedback loop.
On top of it, one property drives a full lifecycle --- `TRIGGER`, `Run`,
`halted`, `Drain` --- and demands the drained bytes equal the oracle:

```haskell
testProperty "drain stream == encodeResult (records ++ terminator)" $ property $ do
  records <- forAll (Gen.list (Range.linear 1 24) genWord)
  term <- forAll genWord
  simDrain records term [True] === encodeResult (records <> [term])
```

And then the test that justifies the entire no-flow-control design: run the
same drain with `txReady` chopped to a stuttering `[True, False, True,
True, False]` and assert the output is **byte-identical**. If the
self-pacing has a leak --- a dropped byte, a duplicated one --- this goes
red. It stays green, which is the proof that the loader may share an
unpaced link with a host and never corrupt a frame.

The robustness cases round it out, each one a design decision made
falsifiable: flip any single bit of a `TRIGGER` and no run ever starts
(property over the flip position); over-load 1100 words and the addresses
saturate at `1023` with exactly 1024 writes; send two `LOAD`s and each
writes from address 0 (the overwrite that makes a failed load harmless);
trigger-halt twice and the ring drains twice (re-runnable). The shape is
the house style, now familiar: a pure model that is the *meaning*, a
streaming machine that is the *silicon*, and property tests that weld the
two together over a fresh shower of inputs every run.[^purestream]

## What we read

Three lives, one clock. `RxControl` listens, peeling a frame layer by
layer and holding one byte back so the trailing CRC never masquerades as
payload, writing each little-endian word straight through to the
instruction store and committing nothing until a separate `TRIGGER` says
go. `Run` waits on a single bit. `Drain` sweeps the ring out in seven
patient phases, dancing with the memory's latency, folding a CRC,
flushing the encoder, and pacing every byte off `txReady` so an unpaced
link never overruns. One `mealy`, one eighteen-field memory that is really
four small ones, one `case` on the phase.

And inside it, everything the series built. The [COBS][cobs] codec,
threaded through the loader's clock in `lDec` and `lEnc` --- the promise
that post made, kept. The [CRC][crc], folded over both directions in
`lCrcRx` and `lCrcTx`. The [UART][top]'s four wires, finally connected.
The two block RAMs, driven on the ports the engine leaves free. The
clock that six pure steps kept deferring to "someone else" lives here, and
the someone has a name.

What the loader loads, triggers, and drains is a *program* --- run by a
machine we have circled for the entire series and never once opened. The
instruction store the loader fills, the trace ring it sweeps, the `halted`
it waits on and the `startOut` it pulses: those are all the engine's, and
the engine is the last door. We open it soon --- but first, a shorter
breath: the two memories the loader has spent this post driving, and that
the engine is about to live between, deserve a look of their own. Then, at
last, we walk through the door.

[^ports]: The collision-free claim is the block-RAM design's
port-ownership contract cashed in. `RxControl`/`Drain` and `Run` are
different lives, never concurrent, so the loader (instruction write, ring
read) and the engine (instruction read, ring write) touch disjoint ports
at disjoint times. The *schedule* is the arbitration --- which is what
lets the loader be one more `mealy` beside the engine rather than a bus
master negotiating for access.

[^mealys]: The design doc reached for `mealyS` --- the State-monad flavour
of `mealy`, where the transition is written in do-notation over `State s`
rather than as an explicit `s -> i -> (s, o)` --- and flagged it as "the
idiom for exactly this long sequential FSM." The shipped code uses plain
`mealy` instead, "matching the engine lift." An honest divergence: the
State-monad sugar would have read a shade cleaner in `drainStep`, but
keeping every top-level block lifted the same way --- engine and loader
both plain `mealy` over an explicit step --- won out: a codebase where
every machine wears the same silhouette reads easier than one where each
picks its favourite sugar.

[^holdback]: The one-byte holdback is a general streaming idiom worth
naming, because it recurs anywhere a stream's last element is special and
unmarked. You cannot know an element is the last until the stream ends, so
if the last one needs different treatment --- a checksum, a terminator, a
flush --- you buffer exactly one and always act on the *previous*, then
handle the straggler when the end arrives. It is the streaming dual of
`init` and `last` on a list: the holdback is `init` (everything but the
last), and the delimiter handler is `last`. One register buys a
one-element look-behind, which is all it takes.

[^writethrough]: Write-through trades atomicity for a BRAM. The
alternative --- buffer the whole program, check its CRC, and commit only on
success --- needs somewhere to hold up to 1024 words, a second
four-kilobyte block RAM, to guard against a failure the protocol already
handles. Because `LOAD_PROGRAM` and `TRIGGER` are separate frames (a
deliberate wire-format choice), a corrupted load never runs: nothing runs
until a good trigger, and a well-behaved host re-sends a failed load ---
overwriting the garbage from address 0 --- before it ever triggers. So the
loader gets atomic-*enough* behaviour for free, and the "two LOADs each
write from 0" test proves the overwrite works.

[^noflow]: The Arty A7's FTDI USB-UART has no RTS/CTS wired to the FPGA ---
a board fact, not a choice --- so there is no hardware flow control in
either direction, and the loader carries none to match. It survives on
three things: the receive side cannot overrun (per-byte work is a handful
of cycles against a ~500-cycle byte period at 2 Mbaud); the drain
self-paces off `txReady`; and the backstop for anything that slips is the
whole-frame [CRC][crc] plus fire-and-forget --- a bad frame is dropped, the
host times out and re-runs, byte-reproducibly. Lower the baud (a top-level
`SNat`) if it ever bites.

[^purestream]: The same two-implementations pattern the [COBS post][cobs]
called the house style, now one layer up. Down there it was pure
`Tamal.Wire.Cobs` beside streaming `Tamal.Loader.Cobs`; up here it is pure
`Tamal.Wire` --- the frame format as list transforms --- beside the
streaming loader. You write the frame codec twice: once as a fold over
lists, where you can *think* without back-pressure or cycle timing, and
once as a clocked machine that must survive a stalling consumer and a
dribbling producer. The property tests weld them, so the gnarly clocked
version is never the only place the format is written down.













