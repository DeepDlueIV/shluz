import { forwardGuestRequest } from '../../../../lib/bridge-proxy';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const maxDuration = 120;

export async function GET(request: Request): Promise<Response> {
  return forwardGuestRequest(request);
}

export async function POST(request: Request): Promise<Response> {
  return forwardGuestRequest(request);
}
