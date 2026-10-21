//+------------------------------------------------------------------+
//|                                             Nexus_TrendLines.mq5 |
//|  Indicatorul „Linii de trend” al aplicației Nexus, pentru MT5.   |
//+------------------------------------------------------------------+
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Același algoritm ca trace(df) din analysis.py, pe ultimele InpBars lumânări ÎNCHISE:
//      1. pivoturi: un maxim e mai mare sau egal cu următoarele 3 maxime și strict mai mare
//         decât precedentele 3 (minimele, invers);
//      2. fiecare pereche de pivoturi (i, j) definește o dreaptă; ea e VALIDĂ dacă niciun close
//         de după primul pivot nu trece de ea: prin maxime nu are close deasupra (rezistență,
//         roșu), prin minime nu are close dedesubt (suport, verde).
//    Valoarea dreptei la lumânarea l e calculată cu aceeași formulă ca în analysis.py:
//    (l − k) / m + n, cu m = (x2 − x1) / (y2 − y1).
//  - Diferența față de aplicație: numărul de lumânări din urmă e un parametru (InpBars).
//  - Calculul e incremental: la fiecare lumânare închisă se verifică doar close-ul nou pe
//    dreptele valide, se scot pivoturile ieșite din fereastră și se adaugă perechile noului
//    pivot (confirmat la 3 lumânări după el). Rezultatul e identic cu recalcularea completă,
//    dar mult mai rapid (contează în Strategy Tester / optimizare).
//  - Buffere (pentru EA, prin iCustom): la fiecare lumânare, cea mai apropiată rezistență și
//    cel mai apropiat suport (dreptele valide până la lumânarea precedentă, prelungite până la
//    ea) și câte drepte valide există. Valorile nu se schimbă după închiderea lumânării.
//  - InpProximityPct face ce face sliderul din aplicație: la N%, o dreaptă se vede dacă
//    distanța dintre capătul ei și prețul curent e cel mult N% din intervalul de preț al
//    lumânărilor analizate (maximul − minimul). 100% = toate.
// =====================================================================================
#property copyright   "Nexus"
#property version     "1.00"
#property description "Linii de trend (trace din analysis.py): drepte valide prin maxime (rezistențe) și minime (suporturi)."
#property description "Parametrul principal: câte lumânări închise din urmă se analizează."
#property indicator_chart_window
#property indicator_buffers 4
#property indicator_plots   4
#property indicator_label1  "Rezistenta"
#property indicator_type1   DRAW_ARROW
#property indicator_color1  clrTomato
#property indicator_label2  "Suport"
#property indicator_type2   DRAW_ARROW
#property indicator_color2  clrMediumSeaGreen
#property indicator_label3  "Linii rezistenta"
#property indicator_type3   DRAW_NONE
#property indicator_label4  "Linii suport"
#property indicator_type4   DRAW_NONE

input int    InpBars         = 1000;               // Lumânări închise din urmă pe care se trasează liniile
input int    InpProximityPct = 50;                 // Apropierea de prețul curent, % (100 = toate liniile)
input bool   InpDrawLines    = true;               // Desenează liniile pe chart
input bool   InpShowLevels   = false;              // Puncte: cea mai apropiată rezistență / suport pe fiecare lumânare
input int    InpHistoryBars  = 2000;               // Lumânări de istoric calculate în buffere (0 = doar ultima)
input color  InpResColor     = clrTomato;          // Culoarea rezistențelor (prin maxime)
input color  InpSupColor     = clrMediumSeaGreen;  // Culoarea suporturilor (prin minime)
input int    InpWidth        = 1;                  // Grosimea liniilor
input bool   InpShowInfo     = true;               // Textul din dreapta sus (câte linii se văd)

double ResBuf[], SupBuf[], ResCnt[], SupCnt[];

// Un pivot: indexul lumânării (0 = cea mai veche de pe chart) și prețul.
struct TLPivot
  {
   int               bar;
   double            price;
  };

// O dreaptă validă, în forma din analysis.py: k = lumânarea primului pivot, n = prețul lui,
// m = (x2 − x1) / (y2 − y1); flat = pivoturi la același preț (m infinit, dreaptă orizontală).
struct TLLine
  {
   int               k;
   double            n;
   double            m;
   bool              flat;
   int               via;      // lumânarea celui de-al doilea pivot
   bool              visible;
  };

TLPivot gMaxP[], gMinP[];
TLLine  gMaxL[], gMinL[];
int     gLast  = -1;   // ultima lumânare închisă procesată
int     gBars  = 1000;
double  gRange = 0;    // maximul − minimul lumânărilor analizate (pentru filtrul de apropiere)
int     gDrawn = 0;    // câte drepte sunt desenate acum
const string PREFIX = "NexusTL_";

//+------------------------------------------------------------------+
int OnInit()
  {
   gBars = MathMax(7, InpBars);
   SetIndexBuffer(0, ResBuf, INDICATOR_DATA);
   SetIndexBuffer(1, SupBuf, INDICATOR_DATA);
   SetIndexBuffer(2, ResCnt, INDICATOR_DATA);
   SetIndexBuffer(3, SupCnt, INDICATOR_DATA);
   for(int p = 0; p < 4; p++)
      PlotIndexSetDouble(p, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   int type = InpShowLevels ? DRAW_ARROW : DRAW_NONE;
   PlotIndexSetInteger(0, PLOT_DRAW_TYPE, type);
   PlotIndexSetInteger(1, PLOT_DRAW_TYPE, type);
   PlotIndexSetInteger(0, PLOT_ARROW, 159);
   PlotIndexSetInteger(1, PLOT_ARROW, 159);
   PlotIndexSetInteger(0, PLOT_LINE_COLOR, InpResColor);
   PlotIndexSetInteger(1, PLOT_LINE_COLOR, InpSupColor);
   IndicatorSetString(INDICATOR_SHORTNAME, "Linii de trend (" + IntegerToString(gBars) + ")");
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   ObjectsDeleteAll(0, PREFIX);
   ChartRedraw();
  }

//+------------------------------------------------------------------+
//| Calculul (ca în analysis.py)                                     |
//+------------------------------------------------------------------+
double LineAt(const TLLine &ln, const int l)
  {
   return ln.flat ? ln.n : (l - ln.k) / ln.m + ln.n;
  }

TLLine MakeLine(const TLPivot &a, const TLPivot &b)
  {
   TLLine ln;
   ln.k = a.bar;
   ln.n = a.price;
   ln.via = b.bar;
   double dy = b.price - a.price;
   ln.flat = (dy == 0.0);
   ln.m = ln.flat ? 0.0 : (b.bar - a.bar) / dy;
   ln.visible = true;
   return ln;
  }

// Dreapta e validă dacă niciun close de după primul pivot (până la L inclusiv) nu trece de ea.
bool IsValid(const TLLine &ln, const bool isMax, const int L, const double &close[])
  {
   for(int l = ln.k + 1; l <= L; l++)
     {
      double v = LineAt(ln, l);
      if(isMax ? (v < close[l]) : (v > close[l]))
         return false;
     }
   return true;
  }

bool IsPivotMax(const int i, const double &high[])
  {
   return high[i] >= high[i + 1] && high[i] >= high[i + 2] && high[i] >= high[i + 3] &&
          high[i] > high[i - 1] && high[i] > high[i - 2] && high[i] > high[i - 3];
  }

bool IsPivotMin(const int i, const double &low[])
  {
   return low[i] <= low[i + 1] && low[i] <= low[i + 2] && low[i] <= low[i + 3] &&
          low[i] < low[i - 1] && low[i] < low[i - 2] && low[i] < low[i - 3];
  }

// Prima lumânare a ferestrei care se termină la L (cele mai noi gBars lumânări închise).
int WindowStart(const int L) { return MathMax(0, L - gBars + 1); }

void AddPivot(TLPivot &arr[], const int bar, const double price)
  {
   int n = ArraySize(arr);
   ArrayResize(arr, n + 1, 256);
   arr[n].bar = bar;
   arr[n].price = price;
  }

void AddLine(TLLine &arr[], const TLLine &ln)
  {
   int n = ArraySize(arr);
   ArrayResize(arr, n + 1, 256);
   arr[n] = ln;
  }

// Noul pivot p formează o pereche cu fiecare pivot mai vechi din fereastră; perechile valide rămân.
void PairWithOlder(TLPivot &piv[], TLLine &lines[], const int p, const double price,
                   const bool isMax, const int L, const double &close[])
  {
   TLPivot np;
   np.bar = p;
   np.price = price;
   for(int q = 0; q < ArraySize(piv); q++)
     {
      TLLine ln = MakeLine(piv[q], np);
      if(IsValid(ln, isMax, L, close))
         AddLine(lines, ln);
     }
   AddPivot(piv, p, price);
  }

void UpdateRange(const int L, const double &high[], const double &low[])
  {
   int S = WindowStart(L);
   double hi = high[S], lo = low[S];
   for(int i = S + 1; i <= L; i++)
     {
      if(high[i] > hi) hi = high[i];
      if(low[i] < lo)  lo = low[i];
     }
   gRange = hi - lo;
  }

// Recalcularea completă pe fereastra care se termină la L (la pornire / la istoric nou).
void FullBuild(const int L, const double &high[], const double &low[], const double &close[])
  {
   ArrayResize(gMaxP, 0);
   ArrayResize(gMinP, 0);
   ArrayResize(gMaxL, 0);
   ArrayResize(gMinL, 0);
   int S = WindowStart(L);
   for(int p = S + 3; p <= L - 3; p++)
     {
      if(IsPivotMax(p, high))
         PairWithOlder(gMaxP, gMaxL, p, high[p], true, L, close);
      if(IsPivotMin(p, low))
         PairWithOlder(gMinP, gMinL, p, low[p], false, L, close);
     }
   UpdateRange(L, high, low);
  }

void DropPivotsBefore(TLPivot &arr[], const int first)
  {
   int w = 0;
   for(int r = 0; r < ArraySize(arr); r++)
      if(arr[r].bar >= first)
         arr[w++] = arr[r];
   ArrayResize(arr, w);
  }

// Scoate dreptele ieșite din fereastră și pe cele sparte de close-ul lumânării L.
void FilterLines(TLLine &arr[], const int first, const bool isMax, const int L, const double &close[])
  {
   int w = 0;
   for(int r = 0; r < ArraySize(arr); r++)
     {
      if(arr[r].k < first)
         continue;
      double v = LineAt(arr[r], L);
      if(isMax ? (v < close[L]) : (v > close[L]))
         continue;
      arr[w++] = arr[r];
     }
   ArrayResize(arr, w);
  }

// O lumânare nouă s-a închis (L): actualizare incrementală.
void Step(const int L, const double &high[], const double &low[], const double &close[])
  {
   int first = WindowStart(L) + 3;   // primul pivot posibil (are 3 lumânări în fața lui)
   DropPivotsBefore(gMaxP, first);
   DropPivotsBefore(gMinP, first);
   FilterLines(gMaxL, first, true, L, close);
   FilterLines(gMinL, first, false, L, close);
   int p = L - 3;                     // pivotul confirmat acum (are și 3 lumânări după el)
   if(p >= first)
     {
      if(IsPivotMax(p, high))
         PairWithOlder(gMaxP, gMaxL, p, high[p], true, L, close);
      if(IsPivotMin(p, low))
         PairWithOlder(gMinP, gMinL, p, low[p], false, L, close);
     }
   UpdateRange(L, high, low);
  }

// Bufferele lumânării L+1: dreptele valide după închiderea lui L, prelungite până la L+1.
void WriteBuffers(const int L, const int rates_total)
  {
   int b = L + 1;
   if(b >= rates_total)
      return;
   double res = EMPTY_VALUE, sup = EMPTY_VALUE;
   for(int i = 0; i < ArraySize(gMaxL); i++)
     {
      double v = LineAt(gMaxL[i], b);
      if(res == EMPTY_VALUE || v < res) res = v;
     }
   for(int i = 0; i < ArraySize(gMinL); i++)
     {
      double v = LineAt(gMinL[i], b);
      if(sup == EMPTY_VALUE || v > sup) sup = v;
     }
   ResBuf[b] = res;
   SupBuf[b] = sup;
   ResCnt[b] = ArraySize(gMaxL);
   SupCnt[b] = ArraySize(gMinL);
  }

//+------------------------------------------------------------------+
//| Desenarea                                                        |
//+------------------------------------------------------------------+
void DrawSet(TLLine &arr[], const string tag, const color clr, const int cur,
             const datetime &time[], int &idx)
  {
   for(int i = 0; i < ArraySize(arr); i++)
     {
      string name = PREFIX + tag + IntegerToString(i);
      ObjectCreate(0, name, OBJ_TREND, 0, time[arr[i].k], arr[i].n, time[cur], LineAt(arr[i], cur));
      ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, InpWidth);
      ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, name, OBJPROP_RAY_LEFT, false);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetString(0, name, OBJPROP_TOOLTIP,
                      (tag == "R" ? "Rezistență" : "Suport") + ": " + TimeToString(time[arr[i].k]) +
                      " → " + TimeToString(time[arr[i].via]));
      arr[i].visible = true;
      idx++;
     }
  }

void DrawLines(const int rates_total, const datetime &time[])
  {
   ObjectsDeleteAll(0, PREFIX);
   int cur = rates_total - 1, idx = 0;
   DrawSet(gMaxL, "R", InpResColor, cur, time, idx);
   DrawSet(gMinL, "S", InpSupColor, cur, time, idx);
   gDrawn = idx;
  }

int FilterSet(TLLine &arr[], const string tag, const double price, const int cur, const int pct)
  {
   int shown = 0;
   double limit = pct / 100.0 * gRange;
   for(int i = 0; i < ArraySize(arr); i++)
     {
      bool vis = pct >= 100 || MathAbs(LineAt(arr[i], cur) - price) <= limit;
      if(vis != arr[i].visible)
        {
         ObjectSetInteger(0, PREFIX + tag + IntegerToString(i), OBJPROP_TIMEFRAMES,
                          vis ? OBJ_ALL_PERIODS : OBJ_NO_PERIODS);
         arr[i].visible = vis;
        }
      if(vis) shown++;
     }
   return shown;
  }

// Ascunde / arată liniile după distanța față de prețul curent (ca sliderul din aplicație).
void ApplyFilter(const double price, const int cur)
  {
   int pct = MathMin(100, MathMax(0, InpProximityPct));
   int sr = FilterSet(gMaxL, "R", price, cur, pct);
   int ss = FilterSet(gMinL, "S", price, cur, pct);
   if(!InpShowInfo)
      return;
   string name = PREFIX + "info";
   if(ObjectFind(0, name) < 0)
     {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_RIGHT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_RIGHT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 10);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, 20);
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 9);
      ObjectSetString(0, name, OBJPROP_FONT, "Segoe UI");
      ObjectSetInteger(0, name, OBJPROP_COLOR, clrSilver);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
     }
   int total = ArraySize(gMaxL) + ArraySize(gMinL);
   string txt = "Linii de trend (" + IntegerToString(gBars) + " lumânări) · " +
                (sr + ss == total ? IntegerToString(total) : IntegerToString(sr + ss) + "/" + IntegerToString(total)) +
                " linii · " + IntegerToString(pct) + "%";
   ObjectSetString(0, name, OBJPROP_TEXT, txt);
   ObjectSetString(0, name, OBJPROP_TOOLTIP,
                   IntegerToString(ArraySize(gMaxL)) + " prin maxime (" + IntegerToString(ArraySize(gMaxP)) +
                   " maxime), " + IntegerToString(ArraySize(gMinL)) + " prin minime (" +
                   IntegerToString(ArraySize(gMinP)) + " minime)");
  }

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total, const int prev_calculated,
                const datetime &time[], const double &open[], const double &high[],
                const double &low[], const double &close[], const long &tick_volume[],
                const long &volume[], const int &spread[])
  {
   if(rates_total < 10)
      return 0;
   int Lend = rates_total - 2;   // ultima lumânare închisă
   bool changed = false;
   if(prev_calculated == 0 || gLast < 0 || gLast > Lend)
     {
      ArrayInitialize(ResBuf, EMPTY_VALUE);
      ArrayInitialize(SupBuf, EMPTY_VALUE);
      ArrayInitialize(ResCnt, EMPTY_VALUE);
      ArrayInitialize(SupCnt, EMPTY_VALUE);
      int L0 = MathMax(6, Lend - MathMax(0, InpHistoryBars));
      FullBuild(L0, high, low, close);
      WriteBuffers(L0, rates_total);
      gLast = L0;
      changed = true;
     }
   while(gLast < Lend)
     {
      gLast++;
      Step(gLast, high, low, close);
      WriteBuffers(gLast, rates_total);
      changed = true;
     }
   if(InpDrawLines)
     {
      if(changed)
         DrawLines(rates_total, time);
      ApplyFilter(close[rates_total - 1], rates_total - 1);
      if(changed)
         ChartRedraw();
     }
   return rates_total;
  }
//+------------------------------------------------------------------+
