/**
 * The one door to the icon set — and now the thing that lets the set be
 * swapped at all.
 *
 * Every file that draws an icon imports it from here rather than from a
 * library, and `src/test/iconLane.test.ts` holds that line. What each
 * name resolves to is decided at RENDER by the active pack, so changing
 * the pack is a preference rather than 236 edits.
 *
 * ONE SET ON SCREEN AT A TIME — the rule, and the reason there is no
 * fallback below. A glyph the active pack does not carry does NOT come
 * from another pack: two vocabularies on one screen is the thing the
 * rule forbids, and it is also how a half-migrated pack would look fine
 * to whoever shipped it. It renders nothing instead, and
 * `iconLane.test.ts` makes that unreachable by proving every pack
 * carries every name in `names.ts`.
 *
 * The wrappers are why nothing here is `export *`. A static re-export
 * binds at build time, which is exactly what a swappable set cannot do.
 * The cost is one component per name, generated from `names.ts` — the
 * single inventory both packs answer to.
 */
import {
  createContext, useContext, useEffect, useState,
  type ComponentType, type ReactNode, type CSSProperties, type MouseEventHandler,
} from 'react';
import { BASE_PACK, loadIconPack } from '../../mods/store/packs/icons';
import type { IconName } from './names';
import type { IconWeightName } from './weight';
import type { IconPackModule } from './pack';

export { ICON_NAMES, type IconName } from './names';
export { ICON_WEIGHTS, type IconWeightName } from './weight';

export type { IconPackModule } from './pack';

/** A pack id — one of `ICON_PACK_IDS` in `mods/store/packs/icons`. A string,
 *  like every other pack-backed axis: the packs are a resource this door
 *  consumes by contract, not a list it keeps, and the registry
 *  sanitises a stored id against the packs' own index. */
export type IconPack = string;

/**
 * What an icon takes.
 *
 * Deliberately narrow, and measured rather than guessed: across 574
 * call sites the app passes `className` (347), `aria-hidden`, `style`,
 * `aria-label` and one `strokeWidth`. `size` is absent BY RULE — the
 * prop writes an `<svg>` attribute no Size multiplier can reach, so an
 * icon is sized by CLASS. Keeping it out of this type is where that
 * stops being a convention and becomes a compile error.
 */
export interface IconProps {
  className?: string;
  style?: CSSProperties;
  /** Lucide's stroke. Phosphor draws named weights and ignores it. */
  strokeWidth?: number;
  onClick?: MouseEventHandler<SVGSVGElement>;
  'aria-hidden'?: boolean | 'true' | 'false';
  'aria-label'?: string;
}

/** A glyph, whichever pack is supplying it. */
export type IconComponent = ComponentType<IconProps>;
/** @deprecated The set is not necessarily lucide's any more — say
 *  `IconComponent`. The same type, so the call sites naming it keep
 *  compiling while they are renamed. */
export type LucideIcon = IconComponent;
/** @deprecated Say `IconProps`. */
export type LucideProps = IconProps;

type PackModule = IconPackModule;

const BASE: PackModule = BASE_PACK.module;
const PackContext = createContext<PackModule>(BASE);

/**
 * Installs the active pack, fetching it if it is not the base one.
 *
 * The pack in hand keeps painting until the next one lands — not the
 * base pack, and not nothing. Swapping to a half-loaded set would show
 * the mixed screen the rule forbids; blanking the UI to avoid that
 * would cost more than the wait it saves.
 */
export function IconPackProvider(
  { pack, weight, children }: { pack: IconPack; weight: IconWeightName; children: ReactNode },
) {
  const [impl, setImpl] = useState<PackModule>(BASE);
  useEffect(() => {
    if (pack === BASE_PACK.id) {
      activePack = BASE; activePackId = BASE_PACK.id; setImpl(BASE); return;
    }
    let live = true;
    void loadIconPack(pack).then((m) => {
      // A name that is not a pack resolves to nothing, and the pack in
      // hand keeps painting — the registry should have refused the id,
      // and blanking the screen for a stale preference is worse.
      if (!live || !m) return;
      activePack = m; activePackId = pack; setImpl(m);
    });
    return () => { live = false; };
  }, [pack]);
  const Weight = impl.Provider;
  return (
    <PackContext.Provider value={impl}>
      <Weight weight={weight}>{children}</Weight>
    </PackContext.Provider>
  );
}

/**
 * What an icon takes when it is rendered to a STRING rather than into
 * the tree.
 *
 * The map draws thousands of POI markers through
 * `renderToStaticMarkup`, where there is no CSS context and therefore
 * no class to size by — a pixel size and a colour are the only way, and
 * this is the one place the size rule genuinely cannot apply. It is a
 * separate type so that stays one documented exception instead of a
 * hole in `IconProps` that all 574 call sites could fall through.
 */
export interface RasterIconProps extends IconProps {
  size: number;
  color?: string;
}

/**
 * The active pack, imperatively — and why that has to exist.
 *
 * `renderToStaticMarkup` runs outside the React tree, so a wrapper
 * rendered there reads the DEFAULT context and would draw the base
 * pack's glyph while the screen around it wore another. That is the
 * mixed screen the rule forbids, arriving through the one door that
 * cannot see the context. So the provider publishes the pack here too,
 * and `rasterGlyph` resolves through it.
 *
 * `activePackId` is exported for cache keys: markers are cached by
 * glyph and size, and a cache that does not know the pack would keep
 * serving the previous set's markers forever.
 */
let activePack: PackModule = BASE;
let activePackId: IconPack = BASE_PACK.id;
export const iconPackId = (): IconPack => activePackId;

export function rasterGlyph(icon: IconComponent): ComponentType<RasterIconProps> | undefined {
  const name = (icon as { displayName?: string }).displayName;
  return name ? (activePack[name] as ComponentType<RasterIconProps> | undefined) : undefined;
}

function glyph(name: IconName): IconComponent {
  const C = (props: IconProps) => {
    const pack = useContext(PackContext);
    const Impl = pack[name] as IconComponent | undefined;
    // No cross-pack fallback — see the note at the top of this file.
    if (!Impl) return <span aria-hidden className={props.className} />;
    return <Impl {...props} />;
  };
  C.displayName = name;
  return C;
}

// ── generated from names.ts ──
export const Activity = glyph('Activity');
export const AlarmClock = glyph('AlarmClock');
export const AlertCircle = glyph('AlertCircle');
export const AlertTriangle = glyph('AlertTriangle');
export const Archive = glyph('Archive');
export const ArchiveRestore = glyph('ArchiveRestore');
export const ArrowDown = glyph('ArrowDown');
export const ArrowLeft = glyph('ArrowLeft');
export const ArrowLeftToLine = glyph('ArrowLeftToLine');
export const ArrowRight = glyph('ArrowRight');
export const ArrowRightLeft = glyph('ArrowRightLeft');
export const ArrowRightToLine = glyph('ArrowRightToLine');
export const ArrowUp = glyph('ArrowUp');
export const ArrowUpCircle = glyph('ArrowUpCircle');
export const ArrowUpDown = glyph('ArrowUpDown');
export const ArrowUpRight = glyph('ArrowUpRight');
export const Award = glyph('Award');
export const BadgeCheck = glyph('BadgeCheck');
export const BadgeDollarSign = glyph('BadgeDollarSign');
export const Ban = glyph('Ban');
export const BarChart3 = glyph('BarChart3');
export const BatteryCharging = glyph('BatteryCharging');
export const BedDouble = glyph('BedDouble');
export const Bell = glyph('Bell');
export const BellOff = glyph('BellOff');
export const BellRing = glyph('BellRing');
export const BookOpen = glyph('BookOpen');
export const Bookmark = glyph('Bookmark');
export const Bot = glyph('Bot');
export const Box = glyph('Box');
export const Boxes = glyph('Boxes');
export const Brain = glyph('Brain');
export const Briefcase = glyph('Briefcase');
export const Building2 = glyph('Building2');
export const Bus = glyph('Bus');
export const Calculator = glyph('Calculator');
export const Calendar = glyph('Calendar');
export const CalendarCheck = glyph('CalendarCheck');
export const CalendarDays = glyph('CalendarDays');
export const CalendarRange = glyph('CalendarRange');
export const Camera = glyph('Camera');
export const Car = glyph('Car');
export const Caravan = glyph('Caravan');
export const ChartColumn = glyph('ChartColumn');
export const ChartLine = glyph('ChartLine');
export const ChartPie = glyph('ChartPie');
export const Check = glyph('Check');
export const CheckCheck = glyph('CheckCheck');
export const CheckCircle2 = glyph('CheckCircle2');
export const CheckSquare = glyph('CheckSquare');
export const ChevronDown = glyph('ChevronDown');
export const ChevronLeft = glyph('ChevronLeft');
export const ChevronRight = glyph('ChevronRight');
export const ChevronUp = glyph('ChevronUp');
export const ChevronsDown = glyph('ChevronsDown');
export const ChevronsDownUp = glyph('ChevronsDownUp');
export const ChevronsUp = glyph('ChevronsUp');
export const ChevronsUpDown = glyph('ChevronsUpDown');
export const Circle = glyph('Circle');
export const CircleAlert = glyph('CircleAlert');
export const CircleCheck = glyph('CircleCheck');
export const CircleDashed = glyph('CircleDashed');
export const CircleDot = glyph('CircleDot');
export const CircleParking = glyph('CircleParking');
export const CircleSlash = glyph('CircleSlash');
export const CircleX = glyph('CircleX');
export const ClipboardCheck = glyph('ClipboardCheck');
export const ClipboardList = glyph('ClipboardList');
export const Clock = glyph('Clock');
export const Clock3 = glyph('Clock3');
export const Cloud = glyph('Cloud');
export const CloudOff = glyph('CloudOff');
export const CloudSnow = glyph('CloudSnow');
export const Cog = glyph('Cog');
export const Coins = glyph('Coins');
export const Columns3 = glyph('Columns3');
export const Contact = glyph('Contact');
export const Copy = glyph('Copy');
export const CornerUpRight = glyph('CornerUpRight');
export const CreditCard = glyph('CreditCard');
export const Crown = glyph('Crown');
export const Database = glyph('Database');
export const Disc = glyph('Disc');
export const DollarSign = glyph('DollarSign');
export const Download = glyph('Download');
export const Droplet = glyph('Droplet');
export const Droplets = glyph('Droplets');
export const ExternalLink = glyph('ExternalLink');
export const Eye = glyph('Eye');
export const EyeOff = glyph('EyeOff');
export const FileClock = glyph('FileClock');
export const FileDown = glyph('FileDown');
export const FileImage = glyph('FileImage');
export const FileSpreadsheet = glyph('FileSpreadsheet');
export const FileText = glyph('FileText');
export const FileVideo = glyph('FileVideo');
export const Files = glyph('Files');
export const Filter = glyph('Filter');
export const Flag = glyph('Flag');
export const Flame = glyph('Flame');
export const FlaskConical = glyph('FlaskConical');
export const Folder = glyph('Folder');
export const FolderOpen = glyph('FolderOpen');
export const Fuel = glyph('Fuel');
export const Gauge = glyph('Gauge');
export const Gift = glyph('Gift');
export const Globe = glyph('Globe');
export const GraduationCap = glyph('GraduationCap');
export const GripVertical = glyph('GripVertical');
export const Group = glyph('Group');
export const Hammer = glyph('Hammer');
export const Handshake = glyph('Handshake');
export const HardDrive = glyph('HardDrive');
export const HardHat = glyph('HardHat');
export const Heart = glyph('Heart');
export const HeartPulse = glyph('HeartPulse');
export const HelpCircle = glyph('HelpCircle');
export const History = glyph('History');
export const Home = glyph('Home');
export const Hourglass = glyph('Hourglass');
export const IdCard = glyph('IdCard');
export const Image = glyph('Image');
export const ImagePlus = glyph('ImagePlus');
export const Inbox = glyph('Inbox');
export const Info = glyph('Info');
export const KeyRound = glyph('KeyRound');
export const Landmark = glyph('Landmark');
export const Layers = glyph('Layers');
export const LayoutDashboard = glyph('LayoutDashboard');
export const LayoutGrid = glyph('LayoutGrid');
export const Lightbulb = glyph('Lightbulb');
export const Link = glyph('Link');
export const Link2 = glyph('Link2');
export const Link2Off = glyph('Link2Off');
export const List = glyph('List');
export const ListChecks = glyph('ListChecks');
export const ListTree = glyph('ListTree');
export const Loader2 = glyph('Loader2');
export const Lock = glyph('Lock');
export const LogOut = glyph('LogOut');
export const Mail = glyph('Mail');
export const Map = glyph('Map');
export const MapPin = glyph('MapPin');
export const MapPinned = glyph('MapPinned');
export const Maximize2 = glyph('Maximize2');
export const Menu = glyph('Menu');
export const Merge = glyph('Merge');
export const MessageCircle = glyph('MessageCircle');
export const MessageSquare = glyph('MessageSquare');
export const Microscope = glyph('Microscope');
export const Minimize2 = glyph('Minimize2');
export const Minus = glyph('Minus');
export const Monitor = glyph('Monitor');
export const MonitorSmartphone = glyph('MonitorSmartphone');
export const MoreVertical = glyph('MoreVertical');
export const Mountain = glyph('Mountain');
export const MoveHorizontal = glyph('MoveHorizontal');
export const OctagonAlert = glyph('OctagonAlert');
export const OctagonX = glyph('OctagonX');
export const Package = glyph('Package');
export const PackageX = glyph('PackageX');
export const Palette = glyph('Palette');
export const PanelLeftClose = glyph('PanelLeftClose');
export const PanelLeftOpen = glyph('PanelLeftOpen');
export const PanelTopClose = glyph('PanelTopClose');
export const Paperclip = glyph('Paperclip');
export const ParkingCircle = glyph('ParkingCircle');
export const ParkingSquare = glyph('ParkingSquare');
export const PenLine = glyph('PenLine');
export const Pencil = glyph('Pencil');
export const Percent = glyph('Percent');
export const Pin = glyph('Pin');
export const PinOff = glyph('PinOff');
export const Plane = glyph('Plane');
export const Play = glyph('Play');
export const Plug = glyph('Plug');
export const Plus = glyph('Plus');
export const Power = glyph('Power');
export const Printer = glyph('Printer');
export const Puzzle = glyph('Puzzle');
export const RadioTower = glyph('RadioTower');
export const Receipt = glyph('Receipt');
export const RefreshCcw = glyph('RefreshCcw');
export const RefreshCw = glyph('RefreshCw');
export const RotateCcw = glyph('RotateCcw');
export const RotateCw = glyph('RotateCw');
export const Route = glyph('Route');
export const Rows2 = glyph('Rows2');
export const Rows3 = glyph('Rows3');
export const Rows4 = glyph('Rows4');
export const Satellite = glyph('Satellite');
export const Save = glyph('Save');
export const Scale = glyph('Scale');
export const ScrollText = glyph('ScrollText');
export const Search = glyph('Search');
export const Send = glyph('Send');
export const Server = glyph('Server');
export const Settings = glyph('Settings');
export const Settings2 = glyph('Settings2');
export const Shield = glyph('Shield');
export const ShieldAlert = glyph('ShieldAlert');
export const ShieldCheck = glyph('ShieldCheck');
export const Ship = glyph('Ship');
export const ShowerHead = glyph('ShowerHead');
export const Sigma = glyph('Sigma');
export const Signature = glyph('Signature');
export const Signpost = glyph('Signpost');
export const Siren = glyph('Siren');
export const SlidersHorizontal = glyph('SlidersHorizontal');
export const Smartphone = glyph('Smartphone');
export const Snowflake = glyph('Snowflake');
export const Sparkles = glyph('Sparkles');
export const Square = glyph('Square');
export const SquareParking = glyph('SquareParking');
export const SquarePen = glyph('SquarePen');
export const Star = glyph('Star');
export const StickyNote = glyph('StickyNote');
export const Store = glyph('Store');
export const Sun = glyph('Sun');
export const Table2 = glyph('Table2');
export const TableProperties = glyph('TableProperties');
export const Tablet = glyph('Tablet');
export const Tag = glyph('Tag');
export const Target = glyph('Target');
export const Thermometer = glyph('Thermometer');
export const ThumbsDown = glyph('ThumbsDown');
export const ThumbsUp = glyph('ThumbsUp');
export const Timer = glyph('Timer');
export const TimerReset = glyph('TimerReset');
export const TrafficCone = glyph('TrafficCone');
export const Trash2 = glyph('Trash2');
export const TrendingDown = glyph('TrendingDown');
export const TrendingUp = glyph('TrendingUp');
export const TriangleAlert = glyph('TriangleAlert');
export const Trophy = glyph('Trophy');
export const Truck = glyph('Truck');
export const Undo2 = glyph('Undo2');
export const Ungroup = glyph('Ungroup');
export const Unlink = glyph('Unlink');
export const Upload = glyph('Upload');
export const UploadCloud = glyph('UploadCloud');
export const User = glyph('User');
export const UserCog = glyph('UserCog');
export const UserPlus = glyph('UserPlus');
export const Users = glyph('Users');
export const Video = glyph('Video');
export const Volume2 = glyph('Volume2');
export const VolumeX = glyph('VolumeX');
export const Wallet = glyph('Wallet');
export const Wand2 = glyph('Wand2');
export const Warehouse = glyph('Warehouse');
export const Wifi = glyph('Wifi');
export const Wrench = glyph('Wrench');
export const X = glyph('X');
export const XCircle = glyph('XCircle');
export const Zap = glyph('Zap');
