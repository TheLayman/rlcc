import { useState, useRef, useEffect } from 'react';
import { format } from 'date-fns';
import { Card } from '@/app/components/ui/card';
import { Button } from '@/app/components/ui/button';
import { Badge } from '@/app/components/ui/badge';
import { Separator } from '@/app/components/ui/separator';
import { ScrollArea } from '@/app/components/ui/scroll-area';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/app/components/ui/select';
import { Textarea } from '@/app/components/ui/textarea';
import { Label } from '@/app/components/ui/label';
import {
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Volume2,
  Maximize,
  ArrowLeft,
  Download,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Clock,
} from 'lucide-react';
import { Transaction, ReceiptItem } from '@/lib/mock-data';
import { toast } from 'sonner';

interface VideoPlaybackViewProps {
  transaction: Transaction;
  receiptItems: ReceiptItem[];
  videoMarkers: Array<{ time: number; label: string; type: string }>;
  onBack: () => void;
  onSubmitDecision: (
    transactionId: string,
    status: string,
    category: string,
    notes: string
  ) => void;
}

export function VideoPlaybackView({
  transaction,
  receiptItems,
  videoMarkers,
  onBack,
  onSubmitDecision,
}: VideoPlaybackViewProps) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration] = useState(90);
  const videoContainerRef = useRef<HTMLDivElement>(null);

  const [status, setStatus] = useState<string>('');
  const [fraudCategory, setFraudCategory] = useState<string>('');
  const [notes, setNotes] = useState<string>('');

  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (isPlaying && currentTime < duration) {
      interval = setInterval(() => {
        setCurrentTime((prev) => Math.min(prev + 0.1, duration));
      }, 100);
    } else if (currentTime >= duration) {
      setIsPlaying(false);
    }
    return () => clearInterval(interval);
  }, [isPlaying, currentTime, duration]);

  const handlePlayPause = () => {
    setIsPlaying(!isPlaying);
  };

  const handleSeek = (time: number) => {
    setCurrentTime(time);
    toast.info(`Jumped to ${time.toFixed(1)}s`);
  };

  const handleSkip = (seconds: number) => {
    setCurrentTime(Math.max(0, Math.min(currentTime + seconds, duration)));
  };

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  const handleSubmit = () => {
    if (!status) {
      toast.error('Please select a status');
      return;
    }
    if (status === 'fraudulent' && !fraudCategory) {
      toast.error('Please select a fraud category');
      return;
    }
    onSubmitDecision(transaction.id, status, fraudCategory, notes);
    toast.success('Decision submitted successfully');
  };

  const handleExport = () => {
    toast.success('Evidence package exported successfully');
  };

  const getCurrentReceiptItem = () => {
    return receiptItems.find(
      (item) =>
        currentTime >= item.timestamp_offset &&
        currentTime < item.timestamp_offset + 5
    );
  };

  const currentItem = getCurrentReceiptItem();

  return (
    <div className="h-screen flex flex-col bg-gray-50">
      {/* Header */}
      <div className="bg-gradient-to-r from-blue-700 via-blue-600 to-blue-800 border-b border-blue-800 p-4 shadow-lg">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Button
              variant="ghost"
              size="sm"
              onClick={onBack}
              className="gap-2 text-white hover:bg-white/10"
            >
              <ArrowLeft className="h-4 w-4" />
              Back to Dashboard
            </Button>
            <Separator orientation="vertical" className="h-6 bg-white/30" />
            <div>
              <h2 className="text-lg font-semibold text-white">
                Transaction Review - {transaction.id}
              </h2>
              <p className="text-sm text-blue-100">
                {format(transaction.timestamp, 'MMMM dd, yyyy HH:mm:ss')}
              </p>
            </div>

            <div className="flex items-center gap-2 ml-4">
              <Button
                variant={status === 'genuine' ? 'default' : 'outline'}
                className={`gap-2 min-w-[120px] ${status === 'genuine'
                  ? 'bg-green-600 hover:bg-green-700 border-green-500 text-white'
                  : 'border-green-300 hover:bg-green-500/20 text-green-100'
                  }`}
                onClick={() => setStatus('genuine')}
              >
                <CheckCircle className="h-4 w-4" />
                Genuine
              </Button>
              <Button
                variant={status === 'fraudulent' ? 'default' : 'outline'}
                className={`gap-2 min-w-[120px] ${status === 'fraudulent'
                  ? 'bg-red-600 hover:bg-red-700 border-red-500 text-white'
                  : 'border-red-300 hover:bg-red-500/20 text-red-100'
                  }`}
                onClick={() => setStatus('fraudulent')}
              >
                <XCircle className="h-4 w-4" />
                Fraudulent
              </Button>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <Button
              variant="outline"
              size="sm"
              className="gap-2 border-white/30 text-white hover:bg-white/10 bg-transparent"
              onClick={handleExport}
            >
              <Download className="h-4 w-4" />
              Export Evidence
            </Button>
          </div>
        </div>
      </div>


      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Video Player Section */}
        <div className="flex-1 flex flex-col p-6 overflow-hidden">
          {/* Video Container */}
          <div
            ref={videoContainerRef}
            className="flex-1 bg-gray-900 rounded-lg overflow-hidden mb-4 relative group shadow-lg"
          >
            {/* Mock video display */}
            <div className="w-full h-full flex items-center justify-center bg-gradient-to-br from-gray-800 to-gray-900">
              <div className="text-center">
                <div className="text-6xl mb-4">🎥</div>
                <p className="text-gray-300">
                  Mock CCTV Footage - {transaction.cam_id}
                </p>
                <p className="text-sm text-gray-400 mt-2">
                  Cashier: {transaction.cashier_name} | POS: {transaction.pos_id}
                </p>
              </div>

              {/* Current item highlight */}
              {currentItem && (
                <div className="absolute bottom-20 left-6 bg-blue-600/90 text-white px-4 py-2 rounded-lg">
                  <div className="text-sm font-semibold">Currently Scanning:</div>
                  <div className="text-lg">{currentItem.name}</div>
                  <div className="text-sm opacity-80">{'\u20B9'}{currentItem.price}</div>
                </div>
              )}

              {/* Event markers overlay */}
              <div className="absolute bottom-20 right-6 space-y-2">
                {videoMarkers
                  .filter(
                    (marker) =>
                      Math.abs(marker.time - currentTime) < 2 &&
                      marker.type === 'fraud'
                  )
                  .map((marker, idx) => (
                    <div
                      key={idx}
                      className="bg-red-600/90 text-white px-4 py-2 rounded-lg animate-pulse"
                    >
                      <div className="flex items-center gap-2">
                        <AlertTriangle className="h-5 w-5" />
                        <div>
                          <div className="font-semibold">ALERT</div>
                          <div className="text-sm">{marker.label}</div>
                        </div>
                      </div>
                    </div>
                  ))}
              </div>
            </div>

            {/* Play button overlay */}
            {!isPlaying && (
              <button
                onClick={handlePlayPause}
                className="absolute inset-0 flex items-center justify-center bg-black/30 opacity-0 group-hover:opacity-100 transition-opacity"
              >
                <div className="bg-blue-600 rounded-full p-6">
                  <Play className="h-12 w-12 text-white fill-white" />
                </div>
              </button>
            )}
          </div>

          {/* Video Controls */}
          <Card className="bg-white border-gray-200 p-4 shadow-sm">
            {/* Timeline with markers */}
            <div className="mb-4">
              <div
                className="relative h-12 bg-gray-100 rounded-lg overflow-hidden cursor-pointer"
                onClick={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  const x = e.clientX - rect.left;
                  const percentage = x / rect.width;
                  handleSeek(percentage * duration);
                }}
              >
                {/* Progress bar */}
                <div
                  className="absolute top-0 left-0 h-full bg-blue-200 transition-all pointer-events-none"
                  style={{ width: `${(currentTime / duration) * 100}%` }}
                />

                {/* Event markers */}
                {videoMarkers.map((marker, idx) => (
                  <button
                    key={idx}
                    className={`absolute top-0 h-full w-1 ${marker.type === 'fraud' ? 'bg-red-500' : 'bg-green-500'
                      } hover:w-2 transition-all group/marker`}
                    style={{ left: `${(marker.time / duration) * 100}%` }}
                    onClick={() => handleSeek(marker.time)}
                  >
                    <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 opacity-0 group-hover/marker:opacity-100 transition-opacity pointer-events-none whitespace-nowrap">
                      <div className="bg-white border border-gray-200 rounded px-2 py-1 text-xs shadow-lg text-gray-700">
                        {marker.label}
                      </div>
                    </div>
                  </button>
                ))}

                {/* Playhead */}
                <div
                  className="absolute top-0 h-full w-1 bg-blue-600 shadow-lg"
                  style={{ left: `${(currentTime / duration) * 100}%` }}
                />

                {/* Timeline labels */}
                <div className="absolute inset-0 flex items-center justify-between px-2 text-xs text-gray-500 pointer-events-none">
                  <span>{formatTime(0)}</span>
                  <span>{formatTime(duration)}</span>
                </div>
              </div>

              {/* Current time display */}
              <div className="flex items-center justify-center mt-2 text-sm text-gray-500">
                <Clock className="h-4 w-4 mr-2" />
                {formatTime(currentTime)} / {formatTime(duration)}
              </div>
            </div>

            {/* Control buttons */}
            <div className="flex items-center justify-center gap-2">
              <Button
                variant="outline"
                size="icon"
                className="border-gray-200"
                onClick={() => handleSkip(-10)}
              >
                <SkipBack className="h-4 w-4" />
              </Button>

              <Button
                variant="default"
                size="icon"
                className="h-12 w-12 bg-blue-600 hover:bg-blue-700"
                onClick={handlePlayPause}
              >
                {isPlaying ? (
                  <Pause className="h-6 w-6" />
                ) : (
                  <Play className="h-6 w-6 ml-1" />
                )}
              </Button>

              <Button
                variant="outline"
                size="icon"
                className="border-gray-200"
                onClick={() => handleSkip(10)}
              >
                <SkipForward className="h-4 w-4" />
              </Button>

              <Separator orientation="vertical" className="h-8 mx-2" />

              <Button variant="outline" size="icon" className="border-gray-200">
                <Volume2 className="h-4 w-4" />
              </Button>

              <Button variant="outline" size="icon" className="border-gray-200">
                <Maximize className="h-4 w-4" />
              </Button>
            </div>
          </Card>
        </div>

        {/* Right Sidebar */}
        <div className="w-96 border-l border-gray-200 flex flex-col bg-white">
          <ScrollArea className="flex-1">
            {/* Digital Receipt */}
            <div className="p-4 border-b border-gray-200">
              <h3 className="font-semibold mb-3 flex items-center gap-2 text-gray-800">
                <span>Digital Receipt</span>
                <Badge variant="outline" className="text-xs border-gray-300">
                  Synced
                </Badge>
              </h3>

              <div className="space-y-2">
                {receiptItems.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => handleSeek(item.timestamp_offset)}
                    className={`w-full text-left p-3 rounded-lg border transition-all ${currentItem?.id === item.id
                      ? 'bg-blue-50 border-blue-300'
                      : item.scanned
                        ? 'bg-gray-50 border-gray-200 hover:bg-gray-100'
                        : 'bg-red-50 border-red-200'
                      }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1">
                        <div className="text-sm font-medium text-gray-800">{item.name}</div>
                        <div className="text-xs text-gray-500 mt-1">
                          Qty: {item.quantity} × {'\u20B9'}{item.price.toFixed(2)}
                        </div>
                        <div className="text-xs text-gray-400 mt-1 flex items-center gap-1">
                          <Clock className="h-3 w-3" />
                          {item.timestamp_offset}s
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="text-sm font-semibold text-gray-800">
                          {'\u20B9'}{(item.quantity * item.price).toFixed(2)}
                        </div>
                        {!item.scanned && (
                          <Badge className="mt-1 text-xs bg-red-50 text-red-700 border-red-200">
                            Not Scanned
                          </Badge>
                        )}
                      </div>
                    </div>
                  </button>
                ))}
              </div>

              <Separator className="my-4" />

              <div className="flex items-center justify-between text-lg font-bold text-gray-800">
                <span>Total:</span>
                <span>{'\u20B9'}{transaction.transaction_total.toFixed(2)}</span>
              </div>
            </div>

            {/* Decision Form */}
            <div className="p-4">
              <h3 className="font-semibold mb-4 text-gray-800">Fraud Assessment</h3>

              <div className="space-y-4">
                {status === 'fraudulent' && (
                  <div>
                    <Label htmlFor="category">Fraud Category *</Label>
                    <Select
                      value={fraudCategory}
                      onValueChange={setFraudCategory}
                    >
                      <SelectTrigger className="bg-gray-50 border-gray-200 mt-1">
                        <SelectValue placeholder="Select category..." />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="under-scanning">
                          Under-scanning
                        </SelectItem>
                        <SelectItem value="missing-item">
                          Missing Item
                        </SelectItem>
                        <SelectItem value="fake-barcode">
                          Fake Barcode
                        </SelectItem>
                        <SelectItem value="cash-theft">Cash Theft</SelectItem>
                        <SelectItem value="unauthorized-override">
                          Unauthorized Override
                        </SelectItem>
                        <SelectItem value="sweethearting">
                          Sweethearting
                        </SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                )}

                <div>
                  <Label htmlFor="notes">Notes</Label>
                  <Textarea
                    id="notes"
                    placeholder="Add any additional observations or context..."
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    className="bg-gray-50 border-gray-200 mt-1 min-h-[100px]"
                  />
                </div>

                <Button
                  onClick={handleSubmit}
                  className="w-full bg-blue-600 hover:bg-blue-700 h-10 text-white"
                >
                  Confirm Decision
                </Button>
              </div>
            </div>
          </ScrollArea>
        </div>
      </div>
    </div>
  );
}
