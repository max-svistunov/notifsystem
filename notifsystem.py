#!/usr/bin/env python3

import argparse
import datetime
import os
import subprocess
import sys
import time
import textwrap
import re
import pprint
import tabulate

JOBFILE = "/tmp/message-jobs.txt"

def valid_time(time_string):
    '''Validate that time is in HHMM or HHMMSS format.'''
    if time_string is None:
        return False

    if len(time_string) == 4:
        try:
            datetime.datetime.strptime(time_string, '%H%M')
            return True
        except ValueError:
            pass

    elif len(time_string) == 6:
        try:
            datetime.datetime.strptime(time_string, '%H%M%S')
            return True
        except ValueError:
            pass

    return False


def valid_date(date_string):
    '''Validate that date is in YYYYMMDD or MMDD format.'''
    if date_string is None:
        return False

    if len(date_string) == 8:
        try:
            datetime.datetime.strptime(date_string, '%Y%m%d')
            return True
        except ValueError:
            pass

    elif len(date_string) == 4:
        try:
            datetime.datetime.strptime(date_string, '%m%d')
            return True
        except ValueError:
            pass

    return False


def past_date(date_string):
    '''Check whether the date is in the past'''
    if len(date_string) == 4:
        return False

    current_date = datetime.datetime.today().strftime('%Y%m%d')

    if date_string <= current_date:
        return True


def check_add(args, parser):
    '''Check arguments if we are adding a notification.'''
    mode = args.add_mode

    if (mode == "at" or mode == "in") and not valid_time(args.time):
        print("Invalid or missing time specification!")
        parser.print_help()
        sys.exit()

    elif mode == "on" and not valid_date(args.time):
        print("Invalid or missing date specification!")
        parser.print_help()
        sys.exit()

    elif mode == "on" and past_date(args.time):
        print("The date should be a future date!")
        sys.exit()

    if args.message is None:
        print("Missing message specification!")
        parser.print_help()
        sys.exit()


def check_list_del(args, parser):
    '''Check arguments if we are listing or deleting a notification.'''
    if args.time is not None or \
       args.sound or \
       args.uptime != 0:
        print("Extraneous options specified with the", args.operation,
              "operation!")
        sys.exit()

    if args.operation == "del" and args.id is None:
        print("ID of the notification to cancel not specified!")
        sys.exit()


def check_arguments(args, parser):
    '''Check that the supplied arguments fit the specified operation.'''
    op = args.operation

    if op == "add":
        check_add(args, parser)
    elif op == "list" or op == "del":
        check_list_del(args, parser)


def parse_arguments():
    '''Parse arguments passed by the user.'''
    parser = argparse.ArgumentParser(description="A Linux notification \
scheduler.", formatter_class=argparse.RawTextHelpFormatter)

    parser.add_argument("-o", "--operation", help="Operation to perform",
                        choices=["add", "list", "del"], default="add")
    parser.add_argument("-am", "--add-mode", help=textwrap.dedent('''\
            Scheduling mode:
            at: at time HHMM[SS]
            in: HHMM[SS] from now
            on: on date [YYYY]MMDD'''), choices=["at", "in", "on"], default="at")
    parser.add_argument("-t", "--time", help="Time (HHMMSS/HHMM) or \
date (YYYYMMDD/MMDD) specification")
    parser.add_argument("-s", "--sound", help="Emit a sound with \
notification", action="store_true", default=False)
    parser.add_argument("-p", "--prioritize", help="List at the top \
when listing notifications.", action="store_true", default=False)
    parser.add_argument("-u", "--uptime", help="For how long is the \
notification shown, 0 for indefinitely", type=int, default=0)
    parser.add_argument("-m", "--message", help="What the notification says")
    parser.add_argument("-a", "--alphabetical", help="List notifications \
alphabetically instead of soonest first (the default is soonest first)",
                        action="store_true", default=False)
    parser.add_argument("-id", help="ID of the notification to cancel")

    if len(sys.argv) == 1:
        parser.print_help()
        parser.exit()

    args = parser.parse_args()

    return args, parser


def parse_time_to_seconds(time_string):
    '''Parse HHMMSS or HHMM into seconds'''
    if len(time_string) == 4:
        time_string = time_string + "00"

    hours = int(time_string[:2]) * 3600
    minutes = int(time_string[2:4]) * 60
    seconds = int(time_string[4:])

    return hours + minutes + seconds


def change_if_targeting_current_minute(at_time, seconds_offset):
    '''Take care of the special case when the notification is scheduled in the
    same minute, for example now is 120030, and target time is 120050'''
    time_string_now = datetime.datetime.today().strftime('%H%M%S')

    if at_time + str(seconds_offset) > time_string_now and \
            at_time == time_string_now[:4]:
        seconds_offset_from_now = str(int(seconds_offset) - int(time_string_now[4:]))
        return 'now', seconds_offset_from_now

    return at_time, seconds_offset


def prepend_year(time_string):
    mmdd_now = datetime.datetime.today().strftime('%m%d')
    yyyy_now = datetime.datetime.today().strftime('%Y')
    yyyy_next = str(int(yyyy_now) + 1)

    if mmdd_now >= time_string[:4]:
        return yyyy_next + time_string


def at_time_and_seconds_offset(time_string):
    # If time_string is mmddHHMM, prepend year
    if len(time_string) == 8:
        prepend_year(time_string)

    # If time_string is yyyymmddHHMM, just prepend '-t' option for 'at' command
    if len(time_string) == 12:
        return " -t " + time_string, "00"

    # If time_string is HHMM, just return with zero offset
    if len(time_string) == 4:
        return time_string, "00"

    # If time_string is HHMMSS, handle scheduling for the same minute
    return change_if_targeting_current_minute(time_string[:4], time_string[4:])


def command_to_execute(at_time, seconds_offset, message, uptime, sound_command):
    return ("echo " +  # We will use echo to feed the job to the at command
           "'export DISPLAY=:0 && " +  # Needed for notification to show up
           "sleep " + seconds_offset + " && "  # at works only with minutes
           "notify-send " +  # Beginning of the notification command
           "-t " + str(uptime * 1000) +  # How soon the notification disappears
           ' "' + message + '"' +  # The message the notification will have
           sound_command +  # The playing of the sound if opted
           "' | at " + at_time)  # Piping the job to at


def write_job_to_file(jobID, scheduled_time, seconds_offset, prioritize, message):
    header = None

    if not os.path.exists(JOBFILE):
        header = "JobID | Scheduled date & time | Seconds offset | Prioritized | Message\n"

    job_line = jobID + " | " + scheduled_time + " | " + \
               str(seconds_offset) + " | " + \
               ("Yes" if prioritize else "No") + " | " + \
               message + "\n"

    with open(JOBFILE, 'a') as jobfile:
        if header:
            jobfile.writelines(header)
        jobfile.writelines(job_line)


def get_scheduled_time(time_string, seconds_offset):
    time_now = datetime.datetime.now()
    HH_now = str(time_now.hour).zfill(2)
    MM_now = str(time_now.minute).zfill(2)

    if time_string[:4] == " -t ":
        datetime_object = datetime.datetime.strptime(time_string, ' -t %Y%m%d%H%M%S')
        return datetime_object.strftime('%Y-%m-%d %H:%M:%S')

    datetime_object = time_now
    if time_string < HH_now + MM_now:
        datetime_object += datetime.timedelta(days=1)
    if time_string != "now":
        datetime_object = datetime_object.replace(hour=int(time_string[:2]))
        datetime_object = datetime_object.replace(minute=int(time_string[2:4]))
    datetime_object = datetime_object.replace(second=int(seconds_offset))

    return datetime_object.strftime('%Y-%m-%d %H:%M:%S')


def add_at(time_string, sound, prioritize, uptime, message):
    '''Schedule a notification using the "at time X" specification.'''
    at_time, seconds_offset = at_time_and_seconds_offset(time_string)

    sound_command = " && mplayer ~/bin/bell.mp3" if sound else ""

    command = command_to_execute(at_time, seconds_offset, message, uptime,
            sound_command)


    output = subprocess.run(command, shell=True, capture_output=True,
            text=True).stderr

    jobID = re.search("job [0-9]+", output).group()[4:]
    scheduled_time = get_scheduled_time(at_time, seconds_offset)
    write_job_to_file(jobID, scheduled_time, seconds_offset, prioritize, message)


def add_delta(time_string):
    time_delta = parse_time_to_seconds(time_string)
    current_time_from_epoch = int(time.time())
    target_time_from_epoch = current_time_from_epoch + time_delta
    target_time = time.strftime('%H%M%S',
                                time.localtime(target_time_from_epoch))

    return target_time


def add_in(time_string, sound, prioritize, uptime, message):
    '''Schedule a notification using the "time X from now" specification.'''
    target_time = add_delta(time_string)
    add_at(target_time, sound, prioritize, uptime, message)


def add_on(date_string, sound, prioritize, uptime, message):
    '''Schedule a notification using the "on (day) X" specification.'''
    target_time = date_string + "0000"
    add_at(target_time, sound, prioritize, uptime, message)


def add_notification(mode, time_string, sound, prioritize, uptime, message):
    '''Schedule a notification.'''
    if mode == "at":
        add_at(time_string, sound, prioritize, uptime, message)
    elif mode == "in":
        add_in(time_string, sound, prioritize, uptime, message)
    else:
        add_on(time_string, sound, prioritize, uptime, message)


def pending_jobIDs():
    atq_output = subprocess.run('atq', shell=True, capture_output=True,
            text=True).stdout

    job_lines = atq_output.splitlines()

    return list(map(lambda line: line.split('\t')[0], job_lines))


def job_entries():
    with open(JOBFILE) as jobfile:
        job_entries = jobfile.readlines()[1:]
    return job_entries


def get_time_left(date_time_obj):
    delta = date_time_obj - datetime.datetime.today()
    time_left = str(delta).split('.', 2)[0]

    if len(time_left) == 7:
        time_left = '0' + time_left
    time_left_seconds = str(delta.seconds)

    return time_left, time_left_seconds


def job_line_into_output_fields(job):
    fields = job.split(" | ")

    jobID, job_time, seconds_offset, prioritize, job_message = fields[0], fields[1], fields[2], fields[3], "".join(fields[4:]).rstrip()

    date_time_obj = datetime.datetime.strptime(job_time, '%Y-%m-%d %H:%M:%S')

    time_when = str(date_time_obj)

    time_left, time_left_seconds = get_time_left(date_time_obj)

    return [jobID, time_when, time_left, time_left_seconds, prioritize, job_message]


def job_rows_to_table():
    pass


def get_job_ID(job_line):
    return re.search("[0-9]+", job_line).group()


def get_jobs_to_list():
    active_IDs = pending_jobIDs()
    jobs_in_file = job_entries()
    jobs_to_list = []

    for job_line in jobs_in_file:
        jobID = get_job_ID(job_line)
        if jobID in active_IDs:
            jobs_to_list.append(job_line)

    return jobs_to_list


def get_notification_entries(alphabetical):
    '''Get list of pending notifications with data to be listed.'''
    jobs_to_list = get_jobs_to_list()
    job_entries = list(map(job_line_into_output_fields, jobs_to_list))

    if alphabetical:
        job_entries.sort(key=lambda job: job[5])
    else:
        job_entries.sort(key=lambda job: job[3])

    job_entries.sort(key=lambda job: job[4], reverse=True)

    for entry in job_entries:
        entry = entry[:4] + entry[5:]

    return job_entries


def list_notifications(alphabetical):
    '''List pending notifications.'''
    notification_entries = get_notification_entries(alphabetical)

    table = tabulate.tabulate(notification_entries, headers=['JobID',
        'Scheduled date & time', 'Time left',
        'Time left in seconds', 'Prioritized', 'Message'])

    if notification_entries:
        print(table)
    else:
        print("No scheduled notifications.")


def delete_notification(jobID):
    '''Delete a notification.'''
    output = subprocess.run("atrm " + jobID, shell=True, capture_output=True,
            text=True).stderr
    if "Cannot find jobid" in output:
        print("Notification does not exist.")
    elif not output:
        print("Notification with ID " + jobID + " deleted.")


def dispatch(args):
    '''Dispatch based on whether the user wants to add a notification,
    list notifications, or cancel a notification.'''
    if args.operation == "add":
        add_notification(args.add_mode, args.time, args.sound, args.prioritize,
                         args.uptime, args.message)
    elif args.operation == "list":
        list_notifications(args.alphabetical)
    elif args.operation == "del":
        delete_notification(args.id)


if __name__ == "__main__":
    args, parser = parse_arguments()
    check_arguments(args, parser)
    dispatch(args)
